import json
import os
import boto3
import io
import pandas as pd
import base64
import re
from datetime import datetime
from PIL import Image, ImageOps
from abc import ABC, abstractmethod
from botocore.exceptions import ClientError
from urllib.parse import unquote_plus

# Clients
s3_client = boto3.client('s3')
bedrock_runtime = boto3.client('bedrock-runtime', region_name='us-east-1')

# --- 1. Repository Pattern (Dependency Injection) ---

class DatabaseRepository(ABC):
    @abstractmethod
    def save_ticket(self, data: dict, scan_path: str):
        pass

class S3MockDatabase(DatabaseRepository):
    """
    Mocks a database by saving records as individual JSON files in S3.
    """
    def __init__(self, bucket_name):
        self.bucket_name = bucket_name

    def save_ticket(self, data: dict, scan_path: str):
        # Add metadata
        data['scan_path'] = scan_path
        data['processed_at'] = datetime.now().isoformat()
        
        # Determine path: weigh_tickets/YYYY/MM/filename.json
        # Expecting scan_path: YYYY/MM/filename.jpg
        path_parts = scan_path.split('/')
        if len(path_parts) >= 3:
            year, month = path_parts[0], path_parts[1]
            filename = os.path.splitext(os.path.basename(scan_path))[0] + ".json"
            key = f"weigh_tickets/{year}/{month}/{filename}"
        else:
            # Fallback if path structure is different
            filename = data.get('ticket_number', 'unknown')
            key = f"weigh_tickets/unsorted/{filename}.json"

        s3_client.put_object(
            Bucket=self.bucket_name,
            Key=key,
            Body=json.dumps(data, indent=2),
            ContentType='application/json'
        )
        print(f"Mock DB: Saved record to s3://{self.bucket_name}/{key}")

# --- 2. Archive Service ---

class ArchiveService:
    """
    Handles renaming and moving images to a structured hierarchy.
    """
    def __init__(self, archive_bucket):
        self.archive_bucket = archive_bucket

    def archive_image(self, source_bucket, source_key, data: dict, original_bytes: bytes):
        # Extract values for archival naming
        def get_val(key, default):
            field = data.get(key, {})
            if isinstance(field, dict):
                return field.get('value', default)
            return str(field) # Fallback if AI returns flat string

        # Pattern: YYYY-MM-DD_{Vendor}_{TicketNumber}.jpg
        date_str = get_val('transaction_date', datetime.now().strftime('%Y-%m-%d'))
        vendor = str(get_val('vendor_name', 'UnknownVendor')).replace(' ', '')
        ticket = str(get_val('ticket_number', 'NoTicket'))
        
        # Ensure date format is YYYY-MM-DD for folder parsing
        try:
            date_obj = datetime.strptime(date_str, '%Y-%m-%d')
        except:
            date_obj = datetime.now()
            date_str = date_obj.strftime('%Y-%m-%d')

        ext = os.path.splitext(source_key)[1]
        if not ext: ext = ".jpg"
        new_filename = f"{date_str}_{vendor}_{ticket}{ext}"
        
        # Path: YYYY/MM/filename.ext
        year_month_path = date_obj.strftime('%Y/%m')
        target_key = f"{year_month_path}/{new_filename}"

        # Content-Type mapping
        content_type = 'image/jpeg'
        ext_lower = ext.lower()
        if ext_lower == '.png': content_type = 'image/png'
        elif ext_lower == '.webp': content_type = 'image/webp'
        elif ext_lower == '.gif': content_type = 'image/gif'

        # Upload original bytes to archive (instead of copy) to ensure we save the raw file
        s3_client.put_object(
            Bucket=self.archive_bucket,
            Key=target_key,
            Body=original_bytes,
            ContentType=content_type
        )
        
        print(f"Archive: Saved {source_key} to s3://{self.archive_bucket}/{target_key}")
        return target_key

# --- 3. Main Handler ---

def lambda_handler(event, context):
    try:
        # Dependency Injection setup
        archive_bucket = os.environ['ARCHIVE_BUCKET']
        mock_db_bucket = os.environ['DATABASE_MOCK_BUCKET']
        output_bucket = os.environ['OUTPUT_BUCKET'] # Define early for error handling
        
        db_repo = S3MockDatabase(mock_db_bucket)
        archiver = ArchiveService(archive_bucket)

        # 1. Parse Event
        record = event['Records'][0]
        input_bucket = record['s3']['bucket']['name']
        file_key = unquote_plus(record['s3']['object']['key'])
        
        print(f"Processing file: {file_key}")
        
        # 2. Download Image
        file_obj = s3_client.get_object(Bucket=input_bucket, Key=file_key)
        original_bytes = file_obj['Body'].read()
        file_content = original_bytes
        
        # 3. Resize and Format Image
        image = Image.open(io.BytesIO(file_content))
        
        # Auto-rotate based on EXIF data (fixes sideways smartphone photos)
        image = ImageOps.exif_transpose(image)
        
        max_dim = 1120
        if max(image.size) > max_dim:
            image.thumbnail((max_dim, max_dim))
            
        img_byte_arr = io.BytesIO()
        fmt = image.format if image.format else 'JPEG'
        # If exif_transpose stripped format or we only rotated, force a valid format
        if not fmt or fmt.upper() not in ['JPEG', 'PNG', 'GIF', 'WEBP']:
            fmt = 'JPEG'
            
        # Convert to RGB if saving as JPEG to avoid transparency errors
        if fmt == 'JPEG' and image.mode in ('RGBA', 'P'):
            image = image.convert('RGB')
            
        image.save(img_byte_arr, format=fmt)
        file_content = img_byte_arr.getvalue()
        
        bedrock_format = fmt.lower()
        if bedrock_format == 'jpeg' or bedrock_format == 'jpg': bedrock_format = 'jpeg'
        elif bedrock_format not in ['png', 'gif', 'webp']: bedrock_format = 'jpeg'

        # 3.5 Auto-Orientation Check via AI
        try:
            orientation_prompt = "Look at this receipt. Is it physically rotated? Reply ONLY with the number: 0 (upright), 90 (rotated clockwise), 180 (upside down), or 270 (rotated counter-clockwise). Do not write any other text."
            orient_response = bedrock_client.converse(
                modelId=model_id,
                messages=[{"role": "user", "content": [{"text": orientation_prompt}, {"image": {"format": bedrock_format, "source": {"bytes": file_content}}}]}],
                inferenceConfig={"maxTokens": 10, "temperature": 0.0}
            )
            angle_str = orient_response['output']['message']['content'][0]['text'].strip()
            angle_match = re.search(r'(0|90|180|270)', angle_str)
            if angle_match:
                angle = int(angle_match.group(1))
                if angle in [90, 180, 270]:
                    print(f"Fixing AI detected rotation: {angle} degrees")
                    # Image.rotate() moves counter-clockwise.
                    # 90 clockwise -> rotate 90 counter-clockwise.
                    image = image.rotate(angle, expand=True)
                    # Resave
                    img_byte_arr = io.BytesIO()
                    if fmt == 'JPEG' and image.mode in ('RGBA', 'P'):
                        image = image.convert('RGB')
                    image.save(img_byte_arr, format=fmt)
                    file_content = img_byte_arr.getvalue()
        except Exception as e:
            print(f"Orientation check skipped or failed: {e}")
        
        # 4. Prompt with Confidence Scores & Strict Formatting
        prompt = """
        Analyze this weigh ticket image. You MUST return a JSON list containing ONE object.
        For EACH field, return an object with "value" (string) and "confidence" (0-100 integer).
        
        VENDOR ALIGNMENT HINTS (Use these rules if the vendor matches):
        - If "CEMEX": Job Location is usually "Ship-to Address", Product is under "Material". If the year on the ticket date is cut off, partially printed, or reads like '202', you MUST assume the year is 2026 (e.g. 02/17/2026).
        - If "Vulcan Materials": Net Weight is often at the bottom right labeled "Net Lbs" (divide by 2000 to get Tons).
        - If "Blue Water Industries": They often don't print the year. Assume the year is 2026. The Ticket Number is literally labeled "Ticket".
        - If "Florida Aggregate": Do NOT confuse "Hours" for "Tons". If the line next to "Tons:" is blank, return an empty string.
        - If "Titan America": The Product Name is explicitly labeled "Product:" halfway down the ticket (e.g., "#89 STONE"). Do NOT grab the location name under the top logo. For the Truck ID, use the number strictly next to "Vehicle:" regardless of its length. Do NOT use the long number next to "Hauler:".

        Fields to extract:
        - ticket_number: (Unique ID on the ticket)
        - transaction_date: (Date in YYYY-MM-DD format. If the year is cut off or missing (e.g., '02/17/202'), assume 2026 BUT YOU MUST SET CONFIDENCE TO 40 so the user checks it.)
        - transaction_time: (Time, e.g., 12:56 PM)
        - vendor_name: (Source company name, e.g., CEMEX, Palm Beach Aggregates)
        - customer_name: (Who the product is for)
        - job_location: (Where it's going)
        - truck_id: (Vehicle ID)
        - product_name: (Material name)
        - net_weight_tons: (Amount in tons. If the space/line next to 'Tons' or 'Net' is completely blank, return "" and 0 confidence. Do NOT grab random unrelated numbers like Hours.)

        Rules:
        1. Return ONLY raw JSON inside [].
        2. All "value" fields must be STRINGS wrapped in double quotes. 
        3. No thousands separators (no commas in numbers).
        4. If a field is not found, return "value": "" and "confidence": 0.
        5. CRITICAL: If you are GUESSING or INFERRING a value because it is cut off, blurry, or missing (like guessing a year from '02/17/202'), you MUST set the "confidence" to 40 or lower. Do not claim 99 confidence for a guess.
        6. CRITICAL: If any number or text is faded, semi-transparent, stamped over, or generally poorly visible but you can still make a best guess, you MUST set the "confidence" to 30 or lower so it gets flagged for review. Do not fallback to a clearer but incorrect number nearby.
        7. CRITICAL: DO NOT INCLUDE ANY CONVERSATIONAL TEXT, GREETINGS, OR MARKDOWN.
        
        Example Format:
        [
            {
                "ticket_number": {"value": "12345", "confidence": 99},
                "vendor_name": {"value": "CEMEX", "confidence": 85},
                "net_weight_tons": {"value": "24.50", "confidence": 95}
            }
        ]
        """
        
        messages = [{"role": "user", "content": [{"text": prompt}, {"image": {"format": bedrock_format, "source": {"bytes": file_content}}}]}]
        
        system_prompt = [{"text": "You are an automated data extraction system. You must output ONLY valid JSON. Do not write any conversational text before or after the JSON list."}]
        
        print("Invoking Bedrock...")
        response = bedrock_runtime.converse(
            modelId=os.environ.get('BEDROCK_MODEL_ID', 'us.meta.llama4-maverick-17b-instruct-v1:0'),
            messages=messages,
            system=system_prompt,
            inferenceConfig={"maxTokens": 2000, "temperature": 0.1}
        )
        
        generated_text = response['output']['message']['content'][0]['text']
        print("Raw AI Response:", generated_text)
        
        # 5. Extract & Clean JSON
        json_pattern = re.search(r'\[.*\]', generated_text, re.DOTALL)
        if not json_pattern:
            # Fallback to single object if list is missing
            json_pattern = re.search(r'\{.*\}', generated_text, re.DOTALL)
            if not json_pattern:
                raise ValueError("No JSON found in AI response")
            data = json.loads(json_pattern.group(0))
        else:
            data_list = json.loads(json_pattern.group(0))
            data = data_list[0] if data_list else {}

        # 6. Archive Image and Save to Mock DB
        scan_path = archiver.archive_image(input_bucket, file_key, data, file_content)
        db_repo.save_ticket(data, scan_path)
        
        # 7. CSV Generation (Primary Output)
        # 7. CSV Generation (Primary Output)
        # Flatten the nested structure for CSV: { "field": "value" }
        flat_data = {}
        for k, v in data.items():
            if isinstance(v, dict) and 'value' in v:
                flat_data[k] = v['value']
            else:
                flat_data[k] = v # Fallback

        df = pd.DataFrame([flat_data])
        # Clean commas if AI ignored instructions
        for col in df.columns:
            if df[col].dtype == 'object':
                df[col] = df[col].astype(str).str.replace(',', '', regex=False)
                df[col] = pd.to_numeric(df[col], errors='ignore')

        output_buffer = io.StringIO()
        df.to_csv(output_buffer, index=False)
        
        # output_bucket already defined at top
        # Replace image extension with .csv
        output_key = os.path.basename(scan_path)
        for ext in ['.jpg', '.jpeg', '.png', '.JPG', '.JPEG', '.PNG']:
            if output_key.endswith(ext):
                output_key = output_key.replace(ext, '.csv')
                break
        if not output_key.endswith('.csv'):
            output_key += '.csv'
        
        s3_client.put_object(
            Bucket=output_bucket,
            Key=output_key,
            Body=output_buffer.getvalue(),
            ContentType='text/csv'
        )

        # 8. Save Status Marker (Maps original filename to renamed results)
        # This allows the Website to find the files even after they are renamed.
        status_key = f"status/{file_key}.json"
        status_data = {
            "status": "complete",
            "original_filename": file_key,
            "renamed_base": os.path.splitext(os.path.basename(scan_path))[0],
            "csv_key": output_key,
            "image_key": scan_path,
            "json_key": f"weigh_tickets/{scan_path.replace(os.path.splitext(scan_path)[1], '.json')}"
        }
        s3_client.put_object(
            Bucket=output_bucket,
            Key=status_key,
            Body=json.dumps(status_data),
            ContentType='application/json'
        )
        print(f"Status Marker: Saved to s3://{output_bucket}/{status_key}")
        
        return {'statusCode': 200, 'body': json.dumps("Success")}
        
    except Exception as e:
        print(f"Error: {str(e)}")
        
        # Write Error Status so Frontend stops polling
        try:
            if 'output_bucket' in locals() and 'file_key' in locals():
                s3_client.put_object(
                    Bucket=output_bucket,
                    Key=f"status/{file_key}.json",
                    Body=json.dumps({"status": "error", "message": str(e)}),
                    ContentType='application/json'
                )
        except:
            pass # Fail silently if we can't write error status
            
        raise e
