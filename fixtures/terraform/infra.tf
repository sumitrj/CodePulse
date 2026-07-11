variable "DB_URL" {
  type = string
}

resource "aws_s3_bucket" "data" {
  bucket = "pulse-data"
}

output "bucket_name" {
  value = "x"
}
