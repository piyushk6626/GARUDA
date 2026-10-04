import os
import boto3
client=boto3.client("s3",endpoint_url=os.environ["AIRSPACE_MINIO_ENDPOINT"],aws_access_key_id=os.environ["AIRSPACE_MINIO_ROOT_USER"],aws_secret_access_key=os.environ["AIRSPACE_MINIO_ROOT_PASSWORD"],region_name="us-east-1")
try: client.head_bucket(Bucket="garuda-airspace-raw")
except Exception: client.create_bucket(Bucket="garuda-airspace-raw")
client.put_object(Bucket="garuda-airspace-raw",Key=".ready",Body=b"ready")
