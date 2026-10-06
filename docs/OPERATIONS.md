# Operating Veritas

What the appliance does by itself, what you should watch, and the few things an administrator does from time to time. Commands assume the Linux package (`sudo veritas …`).

## What Veritas does by itself

| Task | How often | Where to see it |
|---|---|---|
| Backup of all data (databases, license, organization policies, certificates, secrets) | every 24 h, first run 10 min after the service starts; the last 7 are kept | Dashboard → Overview → System Health → *Backups & Chain*; files in `/var/lib/veritas/backups/` |
| Check that each organization's evidence hash chain is intact | with every backup | same place; a broken chain turns the panel red and shows a banner |
| Warning when the license, the TLS certificate or free disk space is running out | continuously (banner at the top of the dashboard) | dashboard banner; license warnings are also written to the service log |

Backups use SQLite's online backup, so they are consistent while Veritas is receiving telemetry. Backup files are readable only by the service account because they contain the user database and signing secrets.

### Settings (environment of the service)

| Setting | Default | Meaning |
|---|---|---|
| `VERITAS_BACKUP_INTERVAL_HOURS` | 24 | hours between backups; `0` turns the schedule off |
| `VERITAS_BACKUP_KEEP` | 7 | how many backups to keep |
| `VERITAS_BACKUP_DIR` | `<data dir>/backups` | where backups go |

Set them with `sudo systemctl edit veritas` (add `[Service]` and `Environment=VERITAS_BACKUP_KEEP=14`), then `sudo systemctl restart veritas`.

### Keep a copy off the machine

A backup on the same disk does not survive losing the disk. Point `VERITAS_BACKUP_DIR` at another disk or a network mount, or copy `/var/lib/veritas/backups/` elsewhere on a schedule (for example with `rsync` from your backup system). A backup contains secrets: store copies as carefully as the server itself.

### Restoring

1. `sudo systemctl stop veritas`
2. Check the file: `python backup.py verify <file>` (shipped with the source tree) or restore into a scratch directory first.
3. Restore, then `sudo systemctl start veritas`.

The evidence chain check runs again at the next scheduled pass; you can also press **Verify Cryptographic Hash Chain** on the Audit Ledger page at any time.

## TLS certificate

Veritas creates a self-signed certificate on first start (valid about 2 years). Browsers show a warning and agents need the certificate (`VERITAS_CA_CERT`). To use a certificate from your own CA or a public CA:

```bash
sudo veritas install-cert /path/to/server-chain.pem /path/to/server-key.pem
```

- The first certificate in the file must be the server certificate; intermediate certificates may follow.
- The key must not be protected by a passphrase (`openssl pkey -in key.pem -out key-nopass.pem`).
- Veritas refuses a key that does not match, a certificate that has expired or is not valid yet, and anything that is not PEM; in that case nothing is changed.
- The previous certificate and key are kept next to the new ones (`server.crt.<timestamp>`).
- The service restarts to load it. The name agents connect to must be listed in the certificate; agents must trust the issuer.

When the certificate has 30 days left the dashboard shows a banner. Renew it with the same command.

## License

The dashboard banner appears 30 days before the license ends (red in the last 7). To renew: send the same machine fingerprint (`sudo veritas fingerprint`) to your Veritas contact, then `sudo veritas license /path/to/new.vlic`.

If a license expires while Veritas is running, the service **keeps running and keeps collecting evidence** (a compliance record should not stop because of paperwork) and shows a red banner. The next restart refuses to start without a valid license.

## Users and passwords

Administrators manage accounts under **Users** (SUPER ADMIN only): add users, change roles, give access to organizations, disable accounts, reset passwords. Passwords need at least 10 characters, must not be a common password and must not contain the username or email name. A reset gives a temporary password shown once; the user must choose their own at the next sign-in. Changing or resetting a password signs that account out everywhere else. At least one active SUPER ADMIN must always remain.

### Lost administrator password

If no administrator can sign in, on the server run:

```bash
sudo veritas reset-password <username>
```

It prints a temporary password. That user must choose a new one at the next sign-in; their other sessions end. The reset is written to the audit log (`PASSWORD_RESET`, by "system (command line)").

### Failed sign-ins

Five wrong passwords for the same account name lock that name for 15 minutes, whatever address they come from (and the same applies to names that do not exist, so nothing is revealed about which accounts exist). The lock is recorded as `ACCOUNT_LOCKED` in the audit log and ends by itself. Sign-in is also limited per address (10 attempts per 5 minutes).
