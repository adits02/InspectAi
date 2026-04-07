#!/usr/bin/env python
"""
Script to re-verify all existing certificates and fix any with invalid status.
This helps fix certificates that were uploaded before the file storage fixes.
"""

import os
import django
import argparse
import tempfile
import time
import logging
from datetime import datetime

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'inspection_system.settings')
django.setup()

from institute.models import certificate
from final_certificate_verification import CertificateVerifier

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s: %(message)s',
    filename='reverify_certificates.log'
)
logger = logging.getLogger(__name__)

def reverify_all_certificates(fix_invalid_only=False, limit=None):
    """Re-verify all certificates in the database"""
    try:
        # Query certificates
        if fix_invalid_only:
            certs = certificate.objects(verified__in=['Invalid', 'Rejected', 'Missing'])
            print(f"[reverify] Found {certs.count()} certificates with Invalid/Rejected/Missing status")
        else:
            certs = certificate.objects()
            print(f"[reverify] Found {certs.count()} total certificates")
        
        if limit:
            certs = certs.limit(limit)
        
        certs_list = list(certs)
        print(f"[reverify] Processing {len(certs_list)} certificates")
        logger.info("Starting verification of %d certificates (fix_invalid_only=%s)", len(certs_list), fix_invalid_only)
        
        # Initialize verifier
        verifier = CertificateVerifier()
        
        success_count = 0
        fail_count = 0
        improved_count = 0
        colleges_processed = set()
        
        for idx, cert in enumerate(certs_list, 1):
            try:
                print(f"\n[reverify] [{idx}/{len(certs_list)}] Processing: {cert.name} (College: {cert.college_name})")
                print(f"[reverify]   Current status: {cert.verified}, Score: {cert.score}")
                
                colleges_processed.add(cert.college_name)
                
                # Try to read file from GridFS
                if not cert.file:
                    print(f"[reverify] ❌ No file attached for {cert.id}")
                    logger.warning("Certificate %s has no file", cert.id)
                    fail_count += 1
                    continue
                
                # Extract file data
                data = None
                
                # Method 1: Try direct read
                try:
                    if hasattr(cert.file, 'seek'):
                        cert.file.seek(0)
                    data = cert.file.read()
                    if data and len(data) > 0:
                        print(f"[reverify] ✅ Read {len(data)} bytes using method 1 (direct read)")
                except Exception as e:
                    print(f"[reverify] ⚠️ Method 1 failed: {e}")
                    data = None
                
                # Method 2: Chunked read
                if not data:
                    try:
                        if hasattr(cert.file, 'seek'):
                            cert.file.seek(0)
                        chunks = []
                        while True:
                            chunk = cert.file.read(1024 * 1024)
                            if not chunk:
                                break
                            chunks.append(chunk)
                        data = b''.join(chunks) if chunks else None
                        if data and len(data) > 0:
                            print(f"[reverify] ✅ Read {len(data)} bytes using method 2 (chunked read)")
                    except Exception as e:
                        print(f"[reverify] ⚠️ Method 2 failed: {e}")
                        data = None
                
                # Method 3: Fresh fetch
                if not data:
                    try:
                        fresh_cert = certificate.objects.with_id(cert.id)
                        if fresh_cert and fresh_cert.file:
                            if hasattr(fresh_cert.file, 'seek'):
                                fresh_cert.file.seek(0)
                            data = fresh_cert.file.read()
                            if data and len(data) > 0:
                                print(f"[reverify] ✅ Read {len(data)} bytes using method 3 (fresh fetch)")
                    except Exception as e:
                        print(f"[reverify] ⚠️ Method 3 failed: {e}")
                        data = None
                
                if not data or len(data) == 0:
                    print(f"[reverify] ❌ Failed to read file for {cert.name} (ID: {cert.id})")
                    logger.error("Failed to read file for certificate %s", cert.id)
                    fail_count += 1
                    continue
                
                # Save to temp file for verification
                tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
                tmp_file.write(data)
                tmp_file.flush()
                tmp_file.close()
                
                # Run verification
                print(f"[reverify]   Running verification...")
                start = time.time()
                
                try:
                    results = verifier.process_certificate(tmp_file.name, metadata_words=[cert.college_name], interactive=False)
                    duration = time.time() - start
                    print(f"[reverify]   Verification completed in {duration:.2f}s")
                    
                    # Extract score
                    old_score = cert.score
                    old_status = cert.verified
                    
                    # Try to get score from various fields
                    authenticity_score = 0
                    if 'authenticity_score' in results:
                        try:
                            authenticity_score = float(results['authenticity_score'])
                        except:
                            pass
                    
                    if authenticity_score == 0 and 'score' in results:
                        try:
                            authenticity_score = float(results['score'])
                        except:
                            pass
                    
                    if authenticity_score == 0:
                        format_details = results.get('format_details', {})
                        if isinstance(format_details, dict) and 'overall_similarity' in format_details:
                            try:
                                authenticity_score = float(format_details['overall_similarity'])
                            except:
                                pass
                    
                    # Get status
                    final_status = results.get('final_status')
                    if not final_status:
                        if authenticity_score >= 80:
                            final_status = 'Valid'
                        elif authenticity_score >= 60:
                            final_status = 'Partially Valid'
                        elif authenticity_score >= 40:
                            final_status = 'Weak Evidence'
                        else:
                            final_status = 'Invalid'
                    
                    # Update certificate
                    cert.verified = final_status
                    cert.score = str(int(round(authenticity_score)))
                    cert.verified_at = datetime.utcnow().isoformat()
                    cert.verified_by = 'system_reverify'
                    
                    format_details = results.get('format_details', {})
                    cert_type = results.get('certificate_type', 'Unknown')
                    text_extracted = results.get('text_extraction', 'Unknown')
                    certificate_status = results.get('certificate_status', 'Unknown')
                    
                    cert.notes = f"Type:{cert_type} | TextExtracted:{text_extracted} | Score:{cert.score}% | Status:{cert.verified} | CertStatus:{certificate_status}"
                    cert.format_details = format_details
                    
                    cert.save()
                    
                    # Check if improved
                    improved = False
                    if old_status in ['Invalid', 'Rejected', 'Missing'] and final_status not in ['Invalid', 'Rejected', 'Missing']:
                        improved = True
                        improved_count += 1
                        print(f"[reverify] ✅✅ IMPROVED: {old_status}→{final_status}, Score: {old_score}→{cert.score}")
                    else:
                        print(f"[reverify]   Status: {old_status}→{final_status}, Score: {old_score}→{cert.score}")
                    
                    logger.info("Certificate %s re-verified: %s→%s (score: %s→%s)", 
                               cert.id, old_status, final_status, old_score, cert.score)
                    success_count += 1
                    
                except Exception as e:
                    print(f"[reverify] ❌ Verification failed: {str(e)[:100]}")
                    logger.error("Verification failed for certificate %s: %s", cert.id, str(e)[:100])
                    fail_count += 1
                
                finally:
                    # Clean up temp file
                    try:
                        os.unlink(tmp_file.name)
                    except:
                        pass
                
            except Exception as e:
                print(f"[reverify] ❌ Error processing certificate: {e}")
                logger.error("Error processing certificate %s: %s", cert.id, e)
                fail_count += 1
        
        # Summary
        print(f"\n{'='*80}")
        print(f"[reverify] REVERIFICATION COMPLETE")
        print(f"{'='*80}")
        print(f"[reverify] Total processed: {len(certs_list)}")
        print(f"[reverify] Successful: {success_count}")
        print(f"[reverify] Failed: {fail_count}")
        print(f"[reverify] Improved status: {improved_count}")
        print(f"[reverify] Colleges processed: {len(colleges_processed)}")
        if colleges_processed:
            print(f"[reverify]   - {', '.join(sorted(colleges_processed))}")
        print(f"{'='*80}")
        
        logger.info("Reverification complete: %d successful, %d failed, %d improved", 
                   success_count, fail_count, improved_count)
        
        return success_count, fail_count, improved_count
        
    except Exception as e:
        print(f"[reverify] ❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        logger.error("Fatal error during reverification: %s", e)
        return 0, 0, 0

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Re-verify all certificates')
    parser.add_argument('--invalid-only', action='store_true', help='Only re-verify certificates with Invalid/Rejected status')
    parser.add_argument('--limit', type=int, help='Limit number of certificates to process')
    parser.add_argument('--yes', action='store_true', help='Skip confirmation prompt')
    args = parser.parse_args()
    
    print("=" * 80)
    print("Certificate Re-Verification Tool")
    print("=" * 80)
    
    if not args.yes:
        mode = "Invalid/Rejected only" if args.invalid_only else "All certificates"
        limit_str = f" (limit: {args.limit})" if args.limit else ""
        print(f"\nMode: {mode}{limit_str}")
        print(f"This will re-verify all affected certificates and update their scores/status.")
        response = input("\nContinue? (yes/no): ").strip().lower()
        
        if response != 'yes':
            print("Cancelled")
            exit(0)
    
    success, fail, improved = reverify_all_certificates(fix_invalid_only=args.invalid_only, limit=args.limit)
    print(f"\nResults: {success} successful, {fail} failed, {improved} improved status")
    print("=" * 80)
