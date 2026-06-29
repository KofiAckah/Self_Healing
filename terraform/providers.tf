provider "aws" {
  region = var.aws_region

  # Tag every resource so the lab stack is easy to find and clean up.
  default_tags {
    tags = {
      Project   = "techstream-self-healing"
      ManagedBy = "terraform"
      Owner     = var.owner
    }
  }
}
