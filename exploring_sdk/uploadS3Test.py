from minio import Minio
from io import BytesIO

client = Minio(
    "s3.hippius.com",
    access_key="",
    secret_key="",
    secure=True,
    region="decentralized",
)

# Create a bucket
# client.make_bucket("my-first-bucket")

# Upload a file
content = b"Hello from the Yuma Capstone Team!"
client.put_object(
    "my-first-bucket",
    "Yuma_Says_Hello.txt",
    BytesIO(content),
    length=len(content),
    content_type="text/plain",
)

print("Uploaded successfully!")