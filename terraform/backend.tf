terraform {
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.70"
    }
  }

  # Remote state in S3 with native state locking (Terraform >= 1.10's
  # `use_lockfile`), so no DynamoDB lock table is needed. The state is shared
  # and CI-usable rather than living in a local file.
  #
  # The bucket is created out-of-band (console / CLI). Replace the placeholder
  # below with the bucket name printed by the bootstrap step, e.g.
  #   techstream-selfhealing-tfstate-<account-id>
  backend "s3" {
    bucket       = "techstream-selfhealing-tfstate-412381768295"
    key          = "techstream/terraform.tfstate"
    region       = "eu-west-1"
    encrypt      = true
    use_lockfile = true
  }
}
