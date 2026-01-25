import json
import os
import boto3
from botocore.config import Config

s3_client = boto3.client('s3', 
                         region_name='us-east-1', # Match template
                         config=Config(signature_version='s3v4'))

def lambda_handler(event, context):
    """
    Generates a pre-signed URL for a direct-to-S3 PUT upload.
    Expects a query parameter 'file' with the desired filename.
    """
    try:
        bucket_name = os.environ['INPUT_BUCKET']
        file_name = event['queryStringParameters'].get('file')
        
        if not file_name:
            return {
                'statusCode': 400,
                'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
                'body': json.dumps({'error': 'Missing "file" parameter'})
            }

        # Generate the pre-signed URL for a PUT operation
        # This allows the user to upload the specific file directly
        presigned_url = s3_client.generate_presigned_url(
            'put_object',
            Params={
                'Bucket': bucket_name,
                'Key': file_name
            },
            ExpiresIn=300 # URL valid for 5 minutes
        )

        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*' # Required for CORS
            },
            'body': json.dumps({
                'upload_url': presigned_url,
                'file_key': file_name
            })
        }
        
    except Exception as e:
        print(f"Error generating pre-signed URL: {str(e)}")
        return {
            'statusCode': 500,
            'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
            'body': json.dumps({'error': 'Failed to generate upload URL'})
        }
