# Authentication (spec 3B). Admin-create-only user pool, a public PKCE client
# for the browser, and a free Cognito prefix domain for the hosted login page.
# Users are created by hand in the console, so their emails stay out of the
# repo and the Terraform state.

locals {
  # The app redirects back to its own root, and logs out to the same URL.
  web_url = "https://${aws_cloudfront_distribution.web.domain_name}/"
}

resource "aws_cognito_user_pool" "main" {
  name                     = "aivideogen"
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]
  mfa_configuration        = "OFF"

  # No self-signup: only the owner creates accounts.
  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  password_policy {
    minimum_length    = 12
    require_lowercase = true
    require_uppercase = true
    require_numbers   = true
    require_symbols   = false
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }
}

# Public client: it runs in a browser, so it has no secret and uses the
# authorization code flow with PKCE.
resource "aws_cognito_user_pool_client" "web" {
  name         = "aivideogen-web"
  user_pool_id = aws_cognito_user_pool.main.id

  generate_secret = false

  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email"]
  supported_identity_providers         = ["COGNITO"]

  callback_urls = [local.web_url]
  logout_urls   = [local.web_url]

  explicit_auth_flows           = ["ALLOW_REFRESH_TOKEN_AUTH"]
  prevent_user_existence_errors = "ENABLED"
  enable_token_revocation       = true
}

resource "aws_cognito_user_pool_domain" "main" {
  domain       = "aivideogen-${local.account_id}"
  user_pool_id = aws_cognito_user_pool.main.id
}
