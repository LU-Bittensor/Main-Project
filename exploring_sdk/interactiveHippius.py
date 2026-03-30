from minio import Minio
from io import BytesIO
import urllib3
import mimetypes

def run_cli():
    print()
    print()
    print(r"+---------------------------------------------------------------------------------+")
    print(r"|                                                                                 |")
    print(r"| $$\      $$\   $$\   $$\                                                        |")
    print(r"| $$ |     $$ |  $$ |  $$ |                                                       |")
    print(r"| $$ |     $$ |  $$ |$$$$$$\    $$$$$$\  $$$$$$$\   $$$$$$$\  $$$$$$\   $$$$$$\   |")
    print(r"| $$ |     $$ |  $$ |\_$$  _|  $$  __$$\ $$  __$$\ $$  _____|$$  __$$\ $$  __$$\  |")
    print(r"| $$ |     $$ |  $$ |  $$ |    $$$$$$$$ |$$ |  $$ |\$$$$$$\  $$ /  $$ |$$ |  \__| |")
    print(r"| $$ |     $$ |  $$ |  $$ |$$\ $$   ____|$$ |  $$ | \____$$\ $$ |  $$ |$$ |       |")
    print(r"| $$$$$$$$\\$$$$$$  |  \$$$$  |\$$$$$$$\ $$ |  $$ |$$$$$$$  |\$$$$$$  |$$ |       |")
    print(r"| \________|\______/    \____/  \_______|\__|  \__|\_______/  \______/ \__|       |")
    print(r"|                                                                                 |")
    print(r"|                                                                                 |")
    print(r"|                                                                                 |")
    print(r"|  $$$$$$\  $$\       $$$$$$\                                                     |")
    print(r"| $$  __$$\ $$ |      \_$$  _|                                                    |")
    print(r"| $$ /  \__|$$ |        $$ |                                                      |")
    print(r"| $$ |      $$ |        $$ |                                                      |")
    print(r"| $$ |      $$ |        $$ |                                                      |")
    print(r"| $$ |  $$\ $$ |        $$ |                                                      |")
    print(r"| \$$$$$$  |$$$$$$$$\ $$$$$$\                                                     |")
    print(r"|  \______/ \________|\______|                                                    |")
    print(r"|                                                                                 |")
    print(r"+---------------------------------------------------------------------------------+")

    print("\nWelcome to the LUtensor Hippius CLI")
    print("-----------------------------------")
    print("Please enter your S3 credentials to get started. These can be found in your Hippius account dashboard.")
    print("Note: your credentials are stored during this session but are not saved permanently. You can exit the CLI at any time.\n")

    agreement = input("Do you agree to the terms and conditions? (y/n): ")
    if agreement.lower() != "y":
        print("You must agree to the terms and conditions to use the CLI. Exiting now.")
        return
    
    while True:
        user_access_key = input("Enter your access key: ")
        user_secret_key = input("Enter your secret key: ")

        if not user_access_key or not user_secret_key:
            print("\nAccess key and secret key cannot be empty. Please provide valid credentials.")
            continue
        break

    client = Minio(
        "s3.hippius.com",
        access_key=user_access_key,
        secret_key=user_secret_key,
        secure=True,
        region="decentralized",
        http_client=urllib3.PoolManager(timeout=urllib3.Timeout(connect=10, read=30))
    )
    while True:
        print("\n\nOPTIONS")
        print("---------------------------")
        print("[1] Upload a file to an existing bucket")
        print("[2] Upload a file to a new bucket")    
        print("[3] Download a file")
        print("[4] View content of a file")
        print("[5] List buckets")
        print("[6] List objects in a bucket")
        print("[7] Exit\n")
        choice = input()

        if choice == "1":
            bucket_name = input("Enter the name you would like for the storage bucket: ")
            exists = client.bucket_exists(bucket_name)
            if not exists:
                print(f"Bucket '{bucket_name}' does not exist. Please choose option 5 to list all buckets or option 2 to create a new bucket and upload a file.")
                continue
            file_path = input("Enter the local path to the file you would like to upload (e.g. /path/to/my_file.txt): ")
            file_name = input("Enter the name you would like for the file in the bucket (e.g. my_file.txt): ")
            file_type, _ = mimetypes.guess_type(file_path)
            file_type = file_type or "application/octet-stream"
            print("Attempting to upload file...")
            try:
                with open(file_path, "rb") as f:
                    file_bytes = f.read()
                client.put_object(
                    bucket_name,
                    file_name,
                    BytesIO(file_bytes),
                    length=len(file_bytes),
                    content_type=file_type,
                )
                print("File uploaded successfully!")
            except Exception as e:
                print(f"File upload failed. Error uploading file: {e}")
                continue

        elif choice == "2":
            bucket_name = input("Enter the name you would like for the storage bucket: ")
            print("Attempting to create bucket...")
            try:
                client.make_bucket(bucket_name)
                print("Bucket created successfully!")
            except Exception as e:
                print(f"Bucket creation failed. Error creating bucket: {e}")
                continue

            file_path = input("Enter the local path to the file you would like to upload (e.g. /path/to/my_file.txt): ")
            file_name = input("Enter the name you would like for the file in the bucket (e.g. my_file.txt): ")
            file_type, _ = mimetypes.guess_type(file_path)
            file_type = file_type or "application/octet-stream"
            print("Attempting to upload file...")
            try:
                with open(file_path, "rb") as f:
                    file_bytes = f.read()
                client.put_object(
                    bucket_name,
                    file_name,
                    BytesIO(file_bytes),
                    length=len(file_bytes),
                    content_type=file_type,
                )
                print("File uploaded successfully!")
            except Exception as e:
                print(f"File upload failed. Error uploading file: {e}")
                continue

        elif choice == "3":
            bucket_name = input("Enter the name of the bucket where the file is stored: ")
            file_name = input("Enter the name of the file you want to download: ")
            save_choice = input("Enter a local path to save the downloaded file (e.g. /path/to/save/my_file.txt): ")

            print("Attempting to download file...")
            try:
                response = client.get_object(bucket_name, file_name)
                print("File downloaded successfully!")
            except Exception as e:
                print(f"File download failed. Error downloading file: {e}")
                response.close()
                response.release_conn()
                continue

            try:
                file_content = response.read()
            except Exception as e:
                print(f"Error reading file content: {e}")
                response.close()
                response.release_conn()
                continue

            try:
                with open(save_choice, "wb") as f:
                    f.write(file_content)
                print(f"File saved successfully to {save_choice}!")
            except Exception as e:
                print(f"File save failed. Error saving file: {e}")
                response.close()
                response.release_conn()
                continue

            view_choice = input("Would you like to view the file content? (y/n): ")
            if view_choice.lower() == "y":
                print(f"File content:\n{file_content.decode()}")
            response.close()
            response.release_conn()

        elif choice == "4":
            bucket_name = input("Enter the name of the bucket where the file is stored: ")
            file_name = input("Enter the name of the file you want to download: ")

            print("Attempting to download file...")
            try:
                response = client.get_object(bucket_name, file_name)
                print("File downloaded successfully!")
            except Exception as e:
                print(f"File download failed. Error downloading file: {e}")
                response.close()
                response.release_conn()
                continue

            try:
                file_content = response.read().decode()
                print(f"File content:\n{file_content}")
            except Exception as e:
                print(f"Error reading file content: {e}")
                response.close()
                response.release_conn()
                continue
            response.close()
            response.release_conn()

        elif choice == "5":
            print("Listing buckets...")
            try:
                buckets = client.list_buckets()
                for bucket in buckets:
                    print(f"Bucket Name: {bucket.name}, Creation Date: {bucket.creation_date}")
            except Exception as e:
                print(f"Error listing buckets: {e}")

        elif choice == "6":
            bucket_name = input("Enter bucket name: ")
            prefix_choice = input("Enter a prefix to filter objects (press enter for no prefix filter): ")
            if prefix_choice.strip() == "":
                try:
                    objects = client.list_objects(bucket_name, recursive=True)
                    for obj in objects:
                        print(f"Object Name: {obj.object_name}, Size: {obj.size} bytes, Last Modified: {obj.last_modified}")
                except Exception as e:
                    print(f"Error listing objects: {e}")
                    continue
            else:
                try:
                    objects = client.list_objects(bucket_name, prefix=prefix_choice, recursive=True)
                    for obj in objects:
                        print(f"Object Name: {obj.object_name}, Size: {obj.size} bytes, Last Modified: {obj.last_modified}")
                except Exception as e:
                    print(f"Error listing objects: {e}")
                    continue
            
        elif choice == "7":
            print("\nCLI session ended")
            break

                

if __name__ == "__main__":
    run_cli()