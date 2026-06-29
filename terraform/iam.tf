# Instance role with least privilege: it can read ONLY the one SSM parameter
# holding the Anthropic key (plus decrypt it with the AWS-managed SSM KMS key).
# No wildcard SSM access, no other permissions.

data "aws_iam_policy_document" "assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "techstream" {
  name               = "techstream-self-healing-ec2"
  assume_role_policy = data.aws_iam_policy_document.assume_role.json
}

locals {
  anthropic_param_arn = format(
    "arn:aws:ssm:%s:%s:parameter%s",
    var.aws_region,
    data.aws_caller_identity.current.account_id,
    var.anthropic_ssm_parameter_name,
  )
}

data "aws_iam_policy_document" "ssm_read" {
  statement {
    sid       = "ReadAnthropicParameterOnly"
    actions   = ["ssm:GetParameter"]
    resources = [local.anthropic_param_arn]
  }

  statement {
    sid       = "DecryptWithSsmManagedKey"
    actions   = ["kms:Decrypt"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${var.aws_region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "ssm_read" {
  name   = "read-anthropic-key"
  role   = aws_iam_role.techstream.id
  policy = data.aws_iam_policy_document.ssm_read.json
}

resource "aws_iam_instance_profile" "techstream" {
  name = "techstream-self-healing"
  role = aws_iam_role.techstream.name
}
