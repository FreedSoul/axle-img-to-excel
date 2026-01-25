import json
import os
import boto3
import io
import pandas as pd
import base64
import re
from datetime import datetime
from PIL import Image
from abc import ABC, abstractmethod
from botocore.exceptions import ClientError

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
        # Pattern: YYYY-MM-DD_{Vendor}_{TicketNumber}.jpg
        date_str = data.get('transaction_date', datetime.now().strftime('%Y-%m-%d'))
        vendor = str(data.get('vendor_name', 'UnknownVendor')).replace(' ', '')
        ticket = str(data.get('ticket_number', 'NoTicket'))
        
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

        # Upload original bytes to archive (instead of copy) to ensure we save the raw file
        s3_client.put_object(
            Bucket=self.archive_bucket,
            Key=target_key,
            Body=original_bytes
        )
        
        print(f"Archive: Saved {source_key} to s3://{self.archive_bucket}/{target_key}")
        return target_key

# --- 3. Main Handler ---

def lambda_handler(event, context):
    try:
        # Dependency Injection setup
        archive_bucket = os.environ['ARCHIVE_BUCKET']
        mock_db_bucket = os.environ['DATABASE_MOCK_BUCKET']
        
        db_repo = S3MockDatabase(mock_db_bucket)
        archiver = ArchiveService(archive_bucket)

        # 1. Parse Event
        record = event['Records'][0]
        input_bucket = record['s3']['bucket']['name']
        file_key = record['s3']['object']['key']
        
        print(f"Processing file: {file_key}")
        
        # 2. Download Image
        file_obj = s3_client.get_object(Bucket=input_bucket, Key=file_key)
        original_bytes = file_obj['Body'].read()
        file_content = original_bytes
        
        # 3. Resize Image (Max 1120px)
        image = Image.open(io.BytesIO(file_content))
        max_dim = 1120
        if max(image.size) > max_dim:
            image.thumbnail((max_dim, max_dim))
            img_byte_arr = io.BytesIO()
            fmt = image.format if image.format else 'JPEG' 
            image.save(img_byte_arr, format=fmt)
            file_content = img_byte_arr.getvalue()
        
        # 4. Prompt with Strict Schema
        prompt = """
        Analyze this weigh ticket image. You MUST return a JSON list containing ONE object with these exact keys:
        - ticket_number: (Unique ID on the ticket)
        - transaction_date: (Date in YYYY-MM-DD format)
        - transaction_time: (Time, e.g., 12:56 PM)
        - vendor_name: (Source company name, e.g., CEMEX, Palm Beach Aggregates)
        - customer_name: (Who the product is for)
        - job_location: (Where it's going)
        - truck_id: (Vehicle ID)
        - product_name: (Material name)
        - net_weight_tons: (Amount in tons)
        
        Rules:
        1. Return ONLY raw JSON inside [].
        2. All numerical values must be STRINGS wrapped in double quotes. 
        3. No thousands separators (no commas in numbers).
        """
        
        bedrock_format = image.format.lower() if image.format else 'jpeg'
        if bedrock_format not in ['jpeg', 'png', 'gif', 'webp']: bedrock_format = 'jpeg'
        if bedrock_format == 'jpg': bedrock_format = 'jpeg'

        messages = [{"role": "user", "content": [{"text": prompt}, {"image": {"format": bedrock_format, "source": {"bytes": file_content}}}]}]
        
        print("Invoking Bedrock...")
        response = bedrock_runtime.converse(
            modelId=os.environ.get('BEDROCK_MODEL_ID', 'us.meta.llama3-2-11b-instruct-v1:0'),
            messages=messages,
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
        scan_path = archiver.archive_image(input_bucket, file_key, data, original_bytes)
        db_repo.save_ticket(data, scan_path)
        
        # 7. CSV Generation (Primary Output)
        df = pd.DataFrame([data])
        # Clean commas if AI ignored instructions
        for col in df.columns:
            if df[col].dtype == 'object':
                df[col] = df[col].astype(str).str.replace(',', '', regex=False)
                df[col] = pd.to_numeric(df[col], errors='ignore')

        output_buffer = io.StringIO()
        df.to_csv(output_buffer, index=False)
        
        output_bucket = os.environ['OUTPUT_BUCKET']
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
        
        return {'statusCode': 200, 'body': json.dumps("Success")}
        
    except Exception as e:
        print(f"Error: {str(e)}")
        raise e
