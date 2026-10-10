# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Report them privately via
[GitHub's private vulnerability reporting](https://github.com/ironiro/corsarr/security/advisories/new).
You'll get an answer as soon as possible; please give a little time for a fix before publishing details.

## Supported versions

Security fixes go into the latest stable release (and the beta channel). Please update before reporting.

## Good to know

- The web interface is meant for your **home network**. Without `ADMIN_PASSWORD` anyone on that network can
  change settings – don't expose port 8787 to the internet.
- API keys and tokens are stored in `config.json` in the data directory (readable only by the service user)
  and in backups, which are AES-encrypted with their own password.
- Webhooks are protected by a shared secret.
