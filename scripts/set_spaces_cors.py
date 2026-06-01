"""Set CORS on the Spaces bucket for browser presigned multipart uploads.

DigitalOcean's Spaces web UI cannot set ExposeHeaders, but the multipart upload
flow needs the browser to read the `ETag` from each part PUT response — so we set
CORS via the S3 API here instead. Re-run this whenever the allowed origins change
(e.g. after attaching the custom domain).

Usage (creds are the Spaces access key, same as the app's S3_* env vars):
    export S3_ACCESS_KEY=DO00...
    export S3_SECRET_KEY=...
    python3 scripts/set_spaces_cors.py \
        --bucket gravitas-mailer-prod \
        --origin https://gravitas-mailer-ww4cc.ondigitalocean.app \
        --origin https://mailer.gravitasleads.io
"""
import argparse
import os
import sys

# Use the bundled lib/ so this runs without a venv (matches run.py).
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(HERE, 'lib')
if os.path.isdir(LIB) and LIB not in sys.path:
    sys.path.insert(0, LIB)

import boto3
from botocore.client import Config


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bucket', default=os.getenv('S3_BUCKET', 'gravitas-mailer-prod'))
    ap.add_argument('--endpoint', default=os.getenv('S3_ENDPOINT_URL', 'https://nyc3.digitaloceanspaces.com'))
    ap.add_argument('--region', default=os.getenv('S3_REGION', 'nyc3'))
    ap.add_argument('--origin', action='append', dest='origins', required=True,
                    help='Allowed origin (repeatable)')
    args = ap.parse_args()

    try:
        key = os.environ['S3_ACCESS_KEY']
        secret = os.environ['S3_SECRET_KEY']
    except KeyError as e:
        sys.exit(f'Missing env var {e}. Export S3_ACCESS_KEY and S3_SECRET_KEY first.')

    client = boto3.client(
        's3',
        endpoint_url=args.endpoint,
        region_name=args.region,
        aws_access_key_id=key,
        aws_secret_access_key=secret,
        config=Config(signature_version='s3v4', s3={'addressing_style': 'virtual'}),
    )

    rule = {
        'AllowedOrigins': args.origins,
        'AllowedMethods': ['GET', 'PUT', 'HEAD'],
        'AllowedHeaders': ['*'],
        'ExposeHeaders': ['ETag'],
        'MaxAgeSeconds': 3000,
    }
    client.put_bucket_cors(Bucket=args.bucket, CORSConfiguration={'CORSRules': [rule]})
    print(f'CORS applied to {args.bucket} for origins: {args.origins}')
    current = client.get_bucket_cors(Bucket=args.bucket)['CORSRules']
    print('Verified current rules:', current)


if __name__ == '__main__':
    main()
