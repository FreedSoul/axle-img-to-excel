import json
import os
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

s3_client = boto3.client('s3', 
                         region_name='us-east-1', # Match template
                         config=Config(signature_version='s3v4'))

def lambda_handler(event, context):
    """
    Main router for the UrlSigner function.
    Paths:
    - /upload-url (GET): Generates PUT URL for upload.
    - /status (GET): Checks if processing is done and returns GET URLs.
    """
    try:
        path = event.get('rawPath', event.get('requestContext', {}).get('http', {}).get('path', ''))
        params = event.get('queryStringParameters', {})
        
        if path == '/upload-url':
            return handle_upload_request(params)
        elif path == '/status':
            return handle_status_request(params)
        elif path == '/save':
            body = json.loads(event.get('body', '{}'))
            return handle_save_request(body)
        else:
            return response(404, {'error': 'Route not found'})
            
    except Exception as e:
        print(f"Error: {str(e)}")
        return response(500, {'error': 'Internal server error'})

def handle_upload_request(params):
    bucket_name = os.environ['INPUT_BUCKET']
    file_name = params.get('file')
    if not file_name:
        return response(400, {'error': 'Missing "file" parameter'})

    url = s3_client.generate_presigned_url(
        'put_object',
        Params={'Bucket': bucket_name, 'Key': file_name},
        ExpiresIn=300
    )
    return response(200, {'upload_url': url})

def handle_status_request(params):
    """
    Checks for a status marker file created by the processor.
    """
    file_name = params.get('file')
    if not file_name:
        return response(400, {'error': 'Missing "file" parameter'})

    output_bucket = os.environ['OUTPUT_BUCKET']
    archive_bucket = os.environ['ARCHIVE_BUCKET']
    status_key = f"status/{file_name}.json"

    try:
        # Check if status marker exists
        response_obj = s3_client.get_object(Bucket=output_bucket, Key=status_key)
        status_data = json.loads(response_obj['Body'].read().decode('utf-8'))
        
        if status_data.get('status') == 'error':
            return response(200, status_data) # Send the error status directly to frontend
        
        # Generate GET URLs
        urls = {
            'csv': generate_get_url(output_bucket, status_data['csv_key']),
            'image': generate_get_url(archive_bucket, status_data['image_key']),
            'json': generate_get_url(archive_bucket, status_data['json_key'])
        }

        return response(200, {
            'status': 'complete',
            'metadata': {
                'csv_key': status_data['csv_key'],
                'image_key': status_data['image_key'],
                'json_key': status_data['json_key'],
                'renamed_base': status_data['renamed_base']
            },
            'download_urls': urls
        })
        
    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchKey':
            return response(200, {'status': 'processing'})
        raise e

def handle_save_request(body):
    """
    Saves corrected JSON and regenerates CSV in S3.
    Expects body: {csv_key, json_key, data}
    """
    csv_key = body.get('csv_key')
    json_key = body.get('json_key')
    data = body.get('data')

    if not all([csv_key, json_key, data]):
        return response(400, {'error': 'Missing required fields: csv_key, json_key, data'})

    output_bucket = os.environ['OUTPUT_BUCKET']
    archive_bucket = os.environ['ARCHIVE_BUCKET']

    try:
        # 1. Save JSON
        s3_client.put_object(
            Bucket=archive_bucket,
            Key=json_key,
            Body=json.dumps(data, indent=2),
            ContentType='application/json'
        )

        # 2. Regenerate CSV (Simple CSV gen to avoid heavy pandas in signer if possible)
        # However, for consistency with processor, let's just do a clean join
        import io
        import csv
        
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=data.keys())
        writer.writeheader()
        writer.writerow(data)
        
        s3_client.put_object(
            Bucket=output_bucket,
            Key=csv_key,
            Body=output.getvalue(),
            ContentType='text/csv'
        )

        return response(200, {'message': 'Success'})

    except Exception as e:
        print(f"Save Error: {str(e)}")
        return response(500, {'error': f'Failed to save: {str(e)}'})

def generate_get_url(bucket, key):
    return s3_client.generate_presigned_url(
        'get_object',
        Params={'Bucket': bucket, 'Key': key},
        ExpiresIn=3600 # 1 hour
    )

def response(status, body):
    return {
        'statusCode': status,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*'
        },
        'body': json.dumps(body)
    }
