import json
import os
import boto3
import io
import pandas as pd
import base64
import re
from PIL import Image
from botocore.exceptions import ClientError

# Clients
s3_client = boto3.client('s3')
bedrock_runtime = boto3.client('bedrock-runtime', region_name='us-east-1') # Adjust region if needed

def lambda_handler(event, context):
    """
    Triggered by S3 Object Created Event.
    Downloads image, sends to Bedrock (Llama 3.2), saves CSV/Excel to Output Bucket.
    """
    try:
        # 1. Parse Event
        record = event['Records'][0]
        input_bucket = record['s3']['bucket']['name']
        file_key = record['s3']['object']['key']
        
        print(f"Processing file: {file_key} from bucket: {input_bucket}")
        
        # 2. Download Image
        file_obj = s3_client.get_object(Bucket=input_bucket, Key=file_key)
        file_content = file_obj['Body'].read()
        
        # 3. Resize Image (Llama 3.2 Constraint: Max 1120px)
        
        # Open image from bytes
        image = Image.open(io.BytesIO(file_content))
        
        # Check and resize if needed
        max_dim = 1120
        if max(image.size) > max_dim:
            print(f"Resizing image from {image.size} to fit within {max_dim}px...")
            image.thumbnail((max_dim, max_dim))
            
            # Save back to bytes
            img_byte_arr = io.BytesIO()
            # Default to JPEG if format is missing (e.g. valid image data but unknown extension)
            fmt = image.format if image.format else 'JPEG' 
            image.save(img_byte_arr, format=fmt)
            file_content = img_byte_arr.getvalue()
            print(f"Resized dimensions: {image.size}")
            
        # 4. Prepare Bedrock Request
        prompt = """
        Analyze this image (which may be an invoice, construction order, or list).
        Extract all tabular data into a structured JSON format.
        
        Rules:
        1. Identify the headers (e.g., Item, Description, Quantity, Price, Total).
        2. If headers are missing, infer them based on context.
        3. Correct apparent OCR/spelling errors based on context (e.g., if 'Gravel' is misspelled).
        4. Return ONLY raw JSON. No markdown formatting, no explanations. 
        5. The JSON should be a list of objects, e.g., [{"Item": "A", "Price": "1000.00"}]
        6. IMPORTANT: Return all values (even numbers) as STRINGS wrapped in double quotes. This avoids JSON parsing errors if you include commas in numbers.
        """
        
        # Detect format for Bedrock (must be 'jpeg', 'png', 'gif', or 'webp')
        bedrock_format = image.format.lower() if image.format else 'jpeg'
        if bedrock_format not in ['jpeg', 'png', 'gif', 'webp']:
            bedrock_format = 'jpeg'
        if bedrock_format == 'jpg': bedrock_format = 'jpeg'

        messages = [
            {
                "role": "user",
                "content": [
                    {"text": prompt},
                    {
                        "image": {
                            "format": bedrock_format,
                            "source": {"bytes": file_content}
                        }
                    }
                ]
            }
        ]
        
        # 4. Invoke Bedrock (via Converse API)
        print("Invoking Bedrock via Converse API...")
        response = bedrock_runtime.converse(
            modelId=os.environ.get('BEDROCK_MODEL_ID', 'us.meta.llama3-2-11b-instruct-v1:0'),
            messages=messages,
            inferenceConfig={"maxTokens": 2000, "temperature": 0.1}
        )
        
        # Parse the 'content' list from Converse response
        generated_text = response['output']['message']['content'][0]['text']
        
        print("Raw AI Response:", generated_text)
        
        # 5. Extract JSON from Response (Robust Regex)
        
        # Look for JSON content between ```json and ``` or just find the first [ ... ]
        json_pattern = re.search(r'\[.*\]', generated_text, re.DOTALL)
        if json_pattern:
            cleaned_json_str = json_pattern.group(0)
        else:
            # Fallback: try to find object {...} if not a list
            json_pattern_obj = re.search(r'\{.*\}', generated_text, re.DOTALL)
            if json_pattern_obj:
                cleaned_json_str = json_pattern_obj.group(0)
            else:
                # Last resort cleanup
                cleaned_json_str = generated_text.replace("```json", "").replace("```", "").strip()

        data = json.loads(cleaned_json_str)
        
        # 6. Convert to Excel
        df = pd.DataFrame(data)
        
        # Robust Cleaning: Remove commas from numeric-looking strings
        # This fixes the "20,000.00" issue at the data level
        for col in df.columns:
            if df[col].dtype == 'object':
                # Replace commas only if the string looks like a number
                # e.g., "20,000.00" -> "20000.00"
                df[col] = df[col].astype(str).str.replace(',', '', regex=False)
                # Try to convert to numeric where possible for Excel
                df[col] = pd.to_numeric(df[col], errors='ignore')
        
        output_buffer = io.BytesIO()
        # writing to .xlsx
        with pd.ExcelWriter(output_buffer, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
        output_buffer.seek(0)
        
        # 7. Upload to Output Bucket
        output_bucket = os.environ['OUTPUT_BUCKET']
        output_key = os.path.splitext(file_key)[0] + ".xlsx"
        
        s3_client.put_object(
            Bucket=output_bucket,
            Key=output_key,
            Body=output_buffer,
            ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        
        print(f"Success! Saved to s3://{output_bucket}/{output_key}")
        
        return {
            'statusCode': 200,
            'body': json.dumps(f"Processed {file_key} successfully")
        }
        
    except Exception as e:
        print(f"Error: {str(e)}")
        # Optional: Upload an error log or send notification
        raise e
