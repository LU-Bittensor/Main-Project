from minio import Minio
from io import BytesIO

client = Minio(
    "s3.hippius.com",
    access_key="access_key_example",
    secret_key="secret_key_example",
    secure=True,
    region="decentralized",
)

# Create a bucket
client.make_bucket("my-first-bucket")

# Upload a file
content = b"Hello from Hippius!"
client.put_object(
    "my-first-bucket",
    "test_file.txt",
    BytesIO(content),
    length=len(content),
    content_type="text/plain",
)

print("Uploaded successfully!")