from minio import Minio
from io import BytesIO

client = Minio(
    "s3.hippius.com",
    access_key="",
    secret_key="",
    secure=True,
    region="decentralized",
)

response = client.get_object("my-first-bucket", "test_file.txt")
print(response.read().decode())
response.close()
response.release_conn()