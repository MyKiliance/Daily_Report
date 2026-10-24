"""Create/open a private issuer link without writing credentials into source control."""
import argparse
import os
from pathlib import Path
import secrets
from workflow import issuer_key

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Create the private issuer link')
    parser.add_argument('--base-url', default='http://localhost:5000')
    parser.add_argument('--vercel', action='store_true', help='Generate a new deployment secret; set it in Vercel before use')
    parser.add_argument('--output', default='issuer-link.txt')
    args = parser.parse_args()
    token = secrets.token_urlsafe(32) if args.vercel else issuer_key(os.environ.get('RAILWAY_VOLUME_MOUNT_PATH', str(Path(__file__).resolve().parent)), False)
    content = 'Private issuer link / ลิงก์ส่วนตัว ห้ามส่งให้ผู้กรอก\n'+args.base_url.rstrip('/')+'/manage#'+token+'\n'
    if args.vercel:
        content += '\nSet Vercel Secret ISSUER_ACCESS_TOKEN (Production) to:\n'+token+'\nThen redeploy. Changing this token revokes all old issuer links.\n'
    Path(args.output).write_text(content, encoding='utf-8')
    print('Saved private access details to '+str(Path(args.output).resolve()))
