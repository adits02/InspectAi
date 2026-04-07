#!/usr/bin/env python
"""
Script to clean up broken certificates from MongoDB.
Run this before re-uploading new certificates.
"""

import os
import django
import argparse

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'inspection_system.settings')
django.setup()

from institute.models import certificate

def cleanup_broken_certificates(force=False):
    """Remove all certificates (and their GridFS files) from the database"""
    try:
        # Count before deletion
        count_before = certificate.objects.count()
        print(f"[cleanup] Found {count_before} certificate(s) in database")
        
        if count_before == 0:
            print("[cleanup] No certificates to clean up")
            return
        
        # Get all college names for reporting
        colleges = set()
        cert_list = []
        
        for cert in certificate.objects():
            colleges.add(cert.college_name)
            cert_list.append({
                'id': str(cert.id),
                'name': cert.name,
                'college': cert.college_name,
                'verified': cert.verified
            })
        
        # Show what will be deleted
        print(f"\n[cleanup] Certificates to be deleted:")
        for cert in cert_list:
            print(f"  - {cert['name']:50s} | College: {cert['college']:30s} | Status: {cert['verified']}")
        
        # Confirm deletion
        if not force:
            response = input(f"\n[cleanup] Delete {count_before} certificate(s)? (yes/no): ").strip().lower()
            
            if response != 'yes':
                print("[cleanup] Cancelled - no changes made")
                return
        
        # Delete all certificates (mongoengine handles GridFS cleanup)
        certificate.objects().delete()
        
        count_after = certificate.objects.count()
        print(f"\n[cleanup] ✅ Successfully deleted {count_before} certificate(s)")
        print(f"[cleanup] Remaining in DB: {count_after}")
        print(f"\n[cleanup] Colleges affected: {', '.join(sorted(colleges))}")
        print("[cleanup] These colleges should now re-upload their certificates")
        
    except Exception as e:
        print(f"[cleanup] ❌ Error during cleanup: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Clean up broken certificates from database')
    parser.add_argument('--yes', action='store_true', help='Skip confirmation prompt')
    args = parser.parse_args()
    
    print("=" * 80)
    print("Certificate Database Cleanup Tool")
    print("=" * 80)
    cleanup_broken_certificates(force=args.yes)
    print("=" * 80)
