import logging
from django.shortcuts import render, redirect
from django.contrib import messages
from django.http import FileResponse, HttpResponseNotFound, JsonResponse
from django.views.decorators.http import require_http_methods
from mongoengine import DoesNotExist
from datetime import datetime
import io

# Configure console logging for immediate terminal output
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
    handlers=[logging.StreamHandler()]
)

# ---------------- MODELS ----------------
from .models import Inspector, Post, Reply, deficiency_report, compliancereport
from inspector.models import Feedback
from core.models import Certificate
from institute.models import mandatory_dis, supporting_document, certificate, Images, InspectionRequest

# ==================================================
# AUTHENTICATION
# ==================================================

def login_view(request):
    if request.method == 'POST':
        user_id = request.POST.get('user_id')
        password = request.POST.get('password')

        try:
            inspector = Inspector.objects.get(user_id=user_id, password=password)
            request.session['user_id'] = inspector.user_id
            request.session['college'] = inspector.college  # Store inspector's college
            return redirect('view_reports')
        except DoesNotExist:
            messages.error(request, 'Invalid credentials')
            return redirect('inspector_login')

    return render(request, 'inspector/inspector_login.html')


def inspector_logout(request):
    request.session.flush()
    return render(request, 'options.html')


# ==================================================
# DASHBOARD
# ==================================================

def view_reports(request):
    return render(request, 'inspector/view_reports.html')


def view_inspection_requests_inspector(request):
    inspector_id = request.session.get('user_id')
    if not inspector_id:
        return redirect('inspector_login')

    inspector = Inspector.objects.get(user_id=inspector_id)
    requests = InspectionRequest.objects(assigned_inspector=inspector.user_id).order_by('-requested_date')

    return render(request, 'inspector/view_inspection_requests.html', {
        'requests': requests,
        'inspector': inspector
    })


def submit_inspection_report(request, request_id):
    inspector_id = request.session.get('user_id')
    if not inspector_id:
        return redirect('inspector_login')

    try:
        inspection_request = InspectionRequest.objects.get(id=request_id)
    except DoesNotExist:
        return render(request, 'inspector/submit_inspection_report.html', {'error': 'Inspection request not found'})

    if inspection_request.assigned_inspector != inspector_id:
        return render(request, 'inspector/submit_inspection_report.html', {'error': 'You are not assigned to this request'})

    if request.method == 'POST':
        report_text = request.POST.get('inspector_report', '').strip()
        report_file = request.FILES.get('inspector_report_file')

        if not report_text and not report_file:
            return render(request, 'inspector/submit_inspection_report.html', {
                'inspection_request': inspection_request,
                'error': 'Please provide a report summary or upload a report file.'
            })

        inspection_request.inspector_report = report_text
        if report_file:
            inspection_request.inspector_report_file.put(report_file, content_type=report_file.content_type)

        inspection_request.status = 'In-Process'
        inspection_request.save()

        messages.success(request, 'Inspection report submitted to admin successfully.')
        return redirect('view_inspection_requests_inspector')

    return render(request, 'inspector/submit_inspection_report.html', {
        'inspection_request': inspection_request
    })


def download_inspection_report(request, request_id):
    try:
        inspection_request = InspectionRequest.objects.get(id=request_id)
        if not inspection_request.inspector_report_file:
            return HttpResponseNotFound('No inspector report file attached')

        raw = inspection_request.inspector_report_file.read() if hasattr(inspection_request.inspector_report_file, 'read') else inspection_request.inspector_report_file
        if not raw:
            return HttpResponseNotFound('Empty report file')

        return FileResponse(io.BytesIO(raw), as_attachment=True, filename=f'inspection_report_{inspection_request.college_name}.pdf')

    except DoesNotExist:
        return HttpResponseNotFound('Inspection request not found')
    except Exception as e:
        print(f"download_inspection_report error: {e}")
        return HttpResponseNotFound('Error retrieving report file')


def view_certificates(request):
    try:
        # Get inspector's college from session
        inspector_college = request.session.get('college')
        
        if not inspector_college:
            messages.warning(request, "College information not found. Please login again.")
            return redirect('inspector_login')
        
        # Filter certificates to show ONLY those from the inspector's college
        uploaded_certificates = certificate.objects(college_name=inspector_college)
        
        return render(
            request,
            'inspector/view_certificates.html',
            {'certificates': uploaded_certificates}
        )
    except Exception as e:
        print(f"[view_certificates] Error: {e}")
        messages.error(request, "Error retrieving certificates")
        return render(request, 'inspector/view_certificates.html')


def download_uploaded_certificate(request, certificate_id):
    try:
        cert = certificate.objects.get(id=certificate_id)
        
        # Verify inspector can only download certificates from their own college
        inspector_college = request.session.get('college')
        if inspector_college != cert.college_name:
            messages.error(request, "Access denied: Certificate belongs to another college")
            return redirect('view_certificates')

        if not cert.file:
            return HttpResponseNotFound("No file found")

        return FileResponse(
            cert.file,
            as_attachment=True,
            filename=f"{cert.name}.pdf"
        )

    except DoesNotExist:
        return HttpResponseNotFound("Certificate not found")


def download_supporting_document(request, document_id):
    try:
        doc = supporting_document.objects.get(id=document_id)

        if not doc.file:
            return HttpResponseNotFound("No file found")

        return FileResponse(
            doc.file,
            as_attachment=True,
            filename=f"{doc.name}.pdf"
        )

    except DoesNotExist:
        return HttpResponseNotFound("Document not found")


@require_http_methods(["POST"])
def verify_certificate(request, certificate_id):
    """
    Run verification on an uploaded certificate and return JSON with the result.
    Adds verbose logging/prints so server terminal shows step-by-step progress.
    """
    import tempfile, os, time, logging
    logger = logging.getLogger(__name__)

    try:
        print(f"[verify_certificate] START id={certificate_id} user={request.session.get('user_id')}")
        logger.info(f"verify_certificate START id={certificate_id} user={request.session.get('user_id')}")

        # Ensure inspector is logged in - return JSON error rather than redirecting to login HTML
        if not request.session.get('user_id'):
            print("[verify_certificate] Authentication required - returning 401")
            logger.warning("Authentication required for verify_certificate")
            return JsonResponse({'error': 'Login required'}, status=401)

        cert = certificate.objects.get(id=certificate_id)
        
        # Verify inspector can only verify certificates from their own college
        inspector_college = request.session.get('college')
        if inspector_college != cert.college_name:
            print(f"[verify_certificate] Access denied: Inspector college '{inspector_college}' != Certificate college '{cert.college_name}'")
            logger.warning("Access denied for certificate id=%s: college mismatch", certificate_id)
            return JsonResponse({'error': 'Access denied: Certificate belongs to another college'}, status=403)

        if not cert.file:
            print("[verify_certificate] No file attached for certificate")
            logger.warning("No file attached for certificate id=%s", certificate_id)
            return JsonResponse({'error': 'No file attached for this certificate.'}, status=404)

        # Save file to a temporary path
        # Handle mongoengine's GridFS file object properly
        try:
            tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
            
            # Read data from mongoengine FileField (GridFS)
            data = None
            
            print(f"[verify_certificate] File object type: {type(cert.file)}")
            print(f"[verify_certificate] File object class name: {cert.file.__class__.__name__}")
            print(f"[verify_certificate] File has seek: {hasattr(cert.file, 'seek')}")
            print(f"[verify_certificate] File has read: {hasattr(cert.file, 'read')}")
            print(f"[verify_certificate] File has _file: {hasattr(cert.file, '_file')}")
            
            logger.info("File object type: %s", type(cert.file))
            
            # Method 1: Try direct read() method with seek
            try:
                print(f"[verify_certificate] Method 1: Trying file.read() after seek(0)")
                if hasattr(cert.file, 'seek'):
                    try:
                        cert.file.seek(0)
                        print(f"[verify_certificate] Successfully seeked to position 0")
                    except Exception as seek_err:
                        print(f"[verify_certificate] ⚠️ Seek failed: {seek_err}")
                
                data = cert.file.read()
                print(f"[verify_certificate] Read returned: type={type(data)}, len={len(data) if data else 'None'}")
                
                if data and len(data) > 0:
                    print(f"[verify_certificate] ✅ Method 1 SUCCESS: Read {len(data)} bytes")
                    logger.info("Read %d bytes using file.read()", len(data))
                else:
                    print(f"[verify_certificate] ⚠️ Method 1 FAILED: read() returned empty/None")
                    data = None
            except Exception as e:
                print(f"[verify_certificate] ⚠️ Method 1 error: {type(e).__name__}: {e}")
                logger.warning("file.read() failed: %s", e)
                data = None
            
            # Method 2: Try to read in chunks if read() failed
            if not data:
                try:
                    print(f"[verify_certificate] Method 2: Trying to read in chunks")
                    if hasattr(cert.file, 'seek'):
                        try:
                            cert.file.seek(0)
                        except:
                            pass
                    
                    chunks = []
                    chunk_size = 1024 * 1024  # 1MB chunks
                    while True:
                        chunk = cert.file.read(chunk_size)
                        if not chunk:
                            break
                        chunks.append(chunk)
                    
                    if chunks:
                        data = b''.join(chunks)
                        print(f"[verify_certificate] ✅ Read {len(data)} bytes in chunks")
                        logger.info("Read %d bytes in chunks", len(data))
                    else:
                        print(f"[verify_certificate] ⚠️ No chunks read")
                except Exception as e:
                    print(f"[verify_certificate] ⚠️ Method 2 (chunked read) failed: {e}")
                    logger.warning("Chunked read failed: %s", e)
                    data = None
            
            # Method 3: Try GridFS direct methods
            if not data:
                try:
                    print(f"[verify_certificate] Method 3: Trying GridFS direct access")
                    if hasattr(cert.file, '_file'):
                        gfs_file = cert.file._file
                        print(f"[verify_certificate] GridFS _file type: {type(gfs_file)}")
                        if hasattr(gfs_file, 'read'):
                            if hasattr(gfs_file, 'seek'):
                                try:
                                    gfs_file.seek(0)
                                except:
                                    pass
                            data = gfs_file.read()
                            print(f"[verify_certificate] Read from _file: {len(data) if data else 0} bytes")
                            if data and len(data) > 0:
                                print(f"[verify_certificate] ✅ Read {len(data)} bytes from GridFS")
                                logger.info("Read %d bytes from GridFS _file", len(data))
                    else:
                        print(f"[verify_certificate] No _file attribute on cert.file")
                except Exception as e:
                    print(f"[verify_certificate] ⚠️ Method 3 failed: {e}")
                    logger.warning("GridFS direct read failed: %s", e)
                    data = None
            
            # Method 4: Re-fetch certificate from database with explicit reload
            if not data:
                try:
                    print(f"[verify_certificate] Method 4: Re-fetching certificate from database")
                    from mongoengine import disconnect, connect
                    # Force a fresh database connection
                    fresh_cert = certificate.objects.with_id(certificate_id)
                    
                    if fresh_cert and fresh_cert.file:
                        print(f"[verify_certificate] Fresh cert file type: {type(fresh_cert.file)}")
                        if hasattr(fresh_cert.file, 'seek'):
                            try:
                                fresh_cert.file.seek(0)
                            except:
                                pass
                        
                        data = fresh_cert.file.read()
                        print(f"[verify_certificate] Read from fresh cert: {len(data) if data else 0} bytes")
                        
                        if data and len(data) > 0:
                            print(f"[verify_certificate] ✅ Read {len(data)} bytes from fresh cert fetch")
                            logger.info("Read %d bytes from fresh cert fetch", len(data))
                        else:
                            print(f"[verify_certificate] ⚠️ Fresh cert read returned empty")
                            data = None
                    else:
                        print(f"[verify_certificate] Fresh cert retrieval failed")
                        data = None
                except Exception as e:
                    print(f"[verify_certificate] ⚠️ Method 4 failed: {e}")
                    logger.warning("Fresh cert fetch failed: %s", e)
                    data = None
            
            # Validation - must have data before proceeding
            if not data or len(data) == 0:
                print(f"[verify_certificate] ❌ Failed to extract file data for id={certificate_id}")
                print(f"[verify_certificate]    All 4 read methods failed")
                print(f"[verify_certificate]    File type: {type(cert.file)}")
                logger.error("Failed to extract file data id=%s after 4 attempts", certificate_id)
                return JsonResponse({
                    'error': 'Certificate file cannot be read. File may not have been uploaded correctly.',
                    'details': 'All read methods failed. The file may need to be re-uploaded.'
                }, status=400)
            
            # Write to temp file and create verification path
            tmp_file.write(data)
            tmp_file.flush()
            tmp_file.close()
            
            print(f"[verify_certificate] ✅ Saved temp file: {tmp_file.name} size={len(data)} bytes")
            logger.info("Saved temp file %s (size=%d)", tmp_file.name, len(data))
            
        except Exception as e:
            print(f"[verify_certificate] ❌ Error processing certificate file: {e}")
            import traceback
            print("[verify_certificate] Traceback:")
            traceback.print_exc()
            logger.error("Error processing certificate file: %s", e)
            return JsonResponse({'error': f'Error processing certificate file: {str(e)}'}, status=500)

        start = time.time()
        print("[verify_certificate] Running CertificateVerifier.process_certificate()")
        logger.info("Running CertificateVerifier.process_certificate for %s", tmp_file.name)

        # Run the verifier (non-interactive)
        from final_certificate_verification import CertificateVerifier
        verifier = CertificateVerifier()
        results = verifier.process_certificate(tmp_file.name, metadata_words=[], interactive=False)

        duration = time.time() - start
        print(f"[verify_certificate] Verifier returned in {duration:.2f}s: {results}")
        logger.info("Verifier results for %s: %s (duration=%.2fs)", certificate_id, results, duration)

        # Extract authenticity score - intelligent composite calculation
        authenticity_score = 0
        detection_score = results.get('detection_score', 0)
        format_similarity = None
        
        # Extract format similarity if available
        format_details = results.get('format_details', {})
        if isinstance(format_details, dict) and 'overall_similarity' in format_details:
            try:
                format_similarity = float(format_details['overall_similarity'])
            except (ValueError, TypeError):
                pass
        
        # STRATEGY: Composite score using available metrics
        # detection_score: certificate type match (0-100, reliable)
        # format_similarity: detailed format match (0-100, reliable)
        # authenticity_score: verifier's conservative assessment (typically low, 0-50)
        
        if detection_score > 0 and format_similarity is not None and format_similarity > 0:
            # Best case: both detection and format available - create composite
            # 50% detection (type match) + 50% format_similarity
            authenticity_score = (detection_score * 0.5) + (format_similarity * 0.5)
            print(f"[verify_certificate] Composite score: {detection_score} (detect) + {format_similarity} (format) = {authenticity_score:.1f}")
            logger.info("Composite score from detection+format: %.1f", authenticity_score)
        elif detection_score > 0:
            # Use detection_score as reliable baseline
            authenticity_score = float(detection_score)
            print(f"[verify_certificate] Using detection_score: {authenticity_score}")
            logger.info("Using detection_score: %.1f", authenticity_score)
        elif format_similarity is not None and format_similarity > 0:
            # Use format_similarity if detection unavailable
            authenticity_score = float(format_similarity)
            print(f"[verify_certificate] Using format_similarity: {authenticity_score}")
            logger.info("Using format_similarity: %.1f", authenticity_score)
        else:
            # FALLBACK: Try 'authenticity_score' field from verifier
            if 'authenticity_score' in results:
                try:
                    authenticity_score = float(results['authenticity_score'])
                    print(f"[verify_certificate] Using verifier authenticity_score: {authenticity_score}")
                    logger.info("Using verifier authenticity_score: %.1f", authenticity_score)
                except (ValueError, TypeError):
                    pass
            
            # FALLBACK 2: Try 'score' field
            if authenticity_score == 0 and 'score' in results:
                try:
                    authenticity_score = float(results['score'])
                    print(f"[verify_certificate] Using score field: {authenticity_score}")
                    logger.info("Using score field: %.1f", authenticity_score)
                except (ValueError, TypeError):
                    pass
            
            # FALLBACK 3: Heuristic as last resort
            if authenticity_score == 0:
                text_extraction = results.get('text_extraction', False)
                format_match = results.get('format_match', False)
                
                if text_extraction and format_match:
                    authenticity_score = 75
                elif text_extraction:
                    authenticity_score = 50
                else:
                    authenticity_score = 20
                
                print(f"[verify_certificate] Using heuristic score: {authenticity_score}")
                logger.info("Using heuristic score: %.1f", authenticity_score)
        
        # Ensure score is numeric and within valid range [0-100]
        try:
            authenticity_score = float(authenticity_score) if authenticity_score is not None else 0
            authenticity_score = max(0, min(100, authenticity_score))  # Clamp to 0-100
        except (ValueError, TypeError) as e:
            print(f"[verify_certificate] ⚠️ Score conversion error: {e}")
            logger.warning("Score conversion error: %s", e)
            authenticity_score = 0
        
        print(f"[verify_certificate] Final verified score: {authenticity_score}")
        logger.info("Final verified score: %s", authenticity_score)
        
        # Determine status based on composite score
        # Thresholds designed for realistic score distribution (40-80 typical range)
        if authenticity_score >= 65:
            cert.verified = 'Verified'
        elif authenticity_score >= 45:
            cert.verified = 'Weak Evidence'
        else:
            cert.verified = 'Rejected'

        print(f"[verify_certificate] Status from score: {cert.verified} (score={authenticity_score:.1f})")
        logger.info("Status from score: %s (score=%.1f)", cert.verified, authenticity_score)
        
        cert.score = str(int(round(authenticity_score)))
        cert.verified_at = datetime.utcnow().isoformat()
        cert.verified_by = request.session.get('user_id', 'system')
        
        format_details = results.get('format_details', {})
        text_extracted = results.get('text_extraction', 'Unknown')
        cert_type = results.get('certificate_type', 'Unknown')
        certificate_status = results.get('certificate_status', 'Unknown')
        
        cert.notes = f"Type:{cert_type} | TextExtracted:{text_extracted} | Score:{cert.score}% | Status:{cert.verified} | CertStatus:{certificate_status}"
        cert.format_details = format_details
        cert.save()

        print(f"[verify_certificate] ✅ Certificate updated: id={certificate_id} | verified={cert.verified} | score={cert.score}%")
        logger.info("Updated certificate %s | verified=%s | score=%s", certificate_id, cert.verified, cert.score)

        # remove temp file
        try:
            os.unlink(tmp_file.name)
            print(f"[verify_certificate] Removed temp file: {tmp_file.name}")
            logger.info("Removed temp file: %s", tmp_file.name)
        except Exception as e:
            print(f"[verify_certificate] Failed to remove temp file: {e}")
            logger.warning("Failed to remove temp file %s: %s", tmp_file.name, e)

        # Regenerate compliance report automatically after certificate verification
        print(f"[verify_certificate] 🔄 Queuing compliance report regeneration for {cert.college_name}...")
        logger.info("Queuing compliance report regeneration for college %s", cert.college_name)
        try:
            import requests
            import threading
            from institute.models import mandatory_dis as mandatory_dis_model
            
            def regenerate_compliance():
                """Regenerate compliance report for all intakes of this college"""
                try:
                    # Find all intakes for this college from mandatory disclosure records
                    try:
                        intake_records = list(mandatory_dis_model.objects(college_name=cert.college_name).distinct('college_intake'))
                    except:
                        intake_records = []
                    
                    if not intake_records:
                        print(f"[verify_certificate] ⚠️ No intake data found in mandatory_dis for {cert.college_name}")
                        print(f"[verify_certificate] Attempting to regenerate with any available intake...")
                        # Try to find from session or use common default
                        intake_records = ['60']  # Default fallback
                    
                    print(f"[verify_certificate] Processing {len(intake_records)} intake(s): {intake_records}")
                    
                    success_count = 0
                    for college_intake in intake_records:
                        try:
                            print(f"[verify_certificate] Regenerating report for intake {college_intake}...")
                            resp = requests.post(
                                "http://localhost:8001/create-compliance-report/",
                                json={
                                    "college_name": cert.college_name, 
                                    "college_intake": str(college_intake)
                                },
                                timeout=180
                            )
                            if resp.status_code == 200:
                                success_count += 1
                                print(f"[verify_certificate] ✅ Compliance report regenerated: {cert.college_name} (Intake: {college_intake})")
                                logger.info("Compliance report regenerated: college=%s intake=%s", cert.college_name, college_intake)
                            else:
                                try:
                                    error_detail = resp.json().get('detail', f'HTTP {resp.status_code}')
                                except:
                                    error_detail = f'HTTP {resp.status_code}'
                                print(f"[verify_certificate] ⚠️ Report generation returned {resp.status_code}: {error_detail}")
                                logger.warning("Report generation status=%s for intake=%s", resp.status_code, college_intake)
                        except requests.exceptions.Timeout:
                            print(f"[verify_certificate] ⚠️ Report generation timeout for intake {college_intake}")
                            logger.warning("Report generation timeout for intake=%s", college_intake)
                        except Exception as e:
                            print(f"[verify_certificate] ⚠️ Report generation error for intake {college_intake}: {str(e)[:100]}")
                            logger.warning("Report generation error for intake=%s: %s", college_intake, str(e)[:100])
                    
                    print(f"[verify_certificate] ✅ Compliance regeneration complete: {success_count}/{len(intake_records)} successful")
                    logger.info("Compliance regeneration complete: %d/%d successful", success_count, len(intake_records))
                    
                except Exception as e:
                    print(f"[verify_certificate] ⚠️ Compliance regeneration error: {str(e)[:100]}")
                    logger.warning("Compliance regeneration error: %s", str(e)[:100])
            
            report_thread = threading.Thread(target=regenerate_compliance, daemon=True)
            report_thread.start()
            print(f"[verify_certificate] ✅ Regeneration thread started")
        except Exception as e:
            print(f"[verify_certificate] ⚠️ Could not start compliance report regeneration: {str(e)[:100]}")
            logger.warning("Could not start compliance report regeneration: %s", str(e)[:100])

        print(f"[verify_certificate] END id={certificate_id}")
        logger.info("verify_certificate END id=%s", certificate_id)

        return JsonResponse({
            'verified': cert.verified,
            'score': cert.score,
            'verified_at': cert.verified_at,
            'notes': cert.notes,
            'details': results
        })

    except DoesNotExist:
        print(f"[verify_certificate] Certificate not found: {certificate_id}")
        logger.error("Certificate not found: %s", certificate_id)
        return JsonResponse({'error': 'Certificate not found'}, status=404)
    except Exception as e:
        print(f"[verify_certificate] Exception: {e}")
        logger.exception("Exception in verify_certificate: %s", e)
        return JsonResponse({'error': str(e)}, status=500)


# ==================================================
# FEEDBACK
# ==================================================

def feedback_page(request):
    return render(request, 'inspector/feedback.html')


def submit_feedback(request):
    if request.method == 'POST':
        inspector_id = request.session.get('user_id')
        feedback_text = request.POST.get('feedback')
        file = request.FILES.get('manual_report')

        if not inspector_id:
            messages.error(request, "Login required")
            return redirect('inspector_login')

        inspector = Inspector.objects.get(user_id=inspector_id)

        if not feedback_text.strip():
            messages.error(request, "Feedback cannot be empty")
            return redirect('feedback_page')

        Feedback(
            inspector_name=inspector.user_id,
            college_name=inspector.college,
            feedback_text=feedback_text,
            manual_report=file
        ).save()

        messages.success(request, "Feedback submitted successfully")
        return redirect('feedback_page')

    return render(request, 'inspector/feedback.html')


# ==================================================
# MANDATORY DISCLOSURE
# ==================================================

def view_mandatory(request):
    try:
        inspector_id = request.session.get('user_id')
        if not inspector_id:
            return redirect('inspector_login')

        inspector = Inspector.objects.get(user_id=inspector_id)
        mandatory_entry = mandatory_dis.objects(college_name=inspector.college).first()

        if not mandatory_entry or not mandatory_entry.file:
            return HttpResponseNotFound("Mandatory disclosure not found")

        return FileResponse(
            mandatory_entry.file,
            as_attachment=True,
            filename=f"{inspector.college}_mandatory.pdf"
        )

    except Exception as e:
        print(e)
        return HttpResponseNotFound("Error downloading file")


# ==================================================
# ALL COLLEGE DOCUMENTS


def view_college_documents(request):
    try:
        inspector_id = request.session.get('user_id')
        if not inspector_id:
            return redirect('inspector_login')

        inspector = Inspector.objects.get(user_id=inspector_id)

        mandatory_entries = mandatory_dis.objects(college_name=inspector.college)
        optional_certs = supporting_document.objects(college_name=inspector.college)

        return render(request, 'inspector/view_college_documents.html', {
            'college': inspector.college,
            'mandatory_entries': mandatory_entries,
            'optional_certs': optional_certs
        })

    except Exception as e:
        print(f"Error in view_college_documents: {e}")
        return render(request, 'inspector/view_college_documents.html', {
            'error': 'Error loading college documents. Please try again.',
            'mandatory_entries': [],
            'optional_certs': []
        })


# ==================================================
# COMPLIANCE REPORT
# ==================================================

def view_compliance(request):
    try:
        inspector_id = request.session.get('user_id')
        if not inspector_id:
            return redirect('inspector_login')

        inspector = Inspector.objects.get(user_id=inspector_id)
        
        # Get compliance report for the college
        compliance = compliancereport.objects(college_name=inspector.college).first()

        if not compliance:
            return render(request, 'inspector/view_compliance.html', {
                'error': 'Compliance report not found for your college. Please ensure mandatory disclosure has been uploaded.',
                'college': inspector.college,
                'report_id': None
            })

        # Pass report data to template, supporting both old and new field names
        report_id = str(compliance.id) if compliance.id else None
        intake_value = compliance.intake or compliance.college_intake or 'N/A'
        
        return render(request, 'inspector/view_compliance.html', {
            'compliance': compliance,
            'college': inspector.college,
            'report_id': report_id,
            'intake': intake_value,  # Pass intake separately for template
            'report_available': compliance.report_file is not None
        })

    except Exception as e:
        print(f"Error in view_compliance: {e}")
        import traceback
        traceback.print_exc()
        return render(request, 'inspector/view_compliance.html', {
            'error': f'Error loading compliance report: {str(e)}',
            'report_id': None
        })


def download_compliance_report(request, report_id):
    """Download the compliance report PDF"""
    try:
        from bson import ObjectId
        compliance = compliancereport.objects.get(id=ObjectId(report_id))
        
        if not compliance.report_file:
            return HttpResponseNotFound("Report file not found")
        
        # Read the file content from MongoDB
        file_content = compliance.report_file.read() if hasattr(compliance.report_file, 'read') else compliance.report_file
        
        response = FileResponse(io.BytesIO(file_content), content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="compliance_report_{compliance.college_name}.pdf"'
        return response
        
    except Exception as e:
        print(f"Error downloading compliance report: {e}")
        return HttpResponseNotFound("Error downloading report")


# ==================================================
# DEFICIENCY REPORT
# ==================================================

def view_deficiancy(request):
    try:
        inspector_id = request.session.get('user_id')
        if not inspector_id:
            return redirect('inspector_login')

        inspector = Inspector.objects.get(user_id=inspector_id)
        deficiency = deficiency_report.objects(college=inspector.college).first()

        if not deficiency:
            return render(request, 'inspector/view_deficiency.html', {
                'error': 'Deficiency report not found',
                'college': inspector.college
            })

        return render(request, 'inspector/view_deficiency.html', {
            'deficiency': deficiency,
            'college': inspector.college
        })

    except Exception as e:
        print(e)
        return render(request, 'inspector/view_deficiency.html', {
            'error': 'Error loading deficiency report'
        })

def download_deficiency_report(request, report_id):
    """Download the deficiency report PDF"""
    try:
        from bson import ObjectId
        import io
        from django.http import FileResponse, HttpResponseNotFound
        
        deficiency = deficiency_report.objects.get(id=ObjectId(report_id))
        
        if not deficiency.file:
            return HttpResponseNotFound("Report file not found")
        
        # Read the file content from MongoDB
        file_content = deficiency.file.read() if hasattr(deficiency.file, 'read') else deficiency.file
        
        response = FileResponse(io.BytesIO(file_content), content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="deficiency_report_{deficiency.college}_{deficiency.branch}.pdf"'
        return response
        
    except Exception as e:
        print(f"Error downloading deficiency report: {e}")
        return HttpResponseNotFound("Error downloading report")


# ==================================================
# IMAGES
# ==================================================

def view_category_images(request, category):
    college = request.session.get('college_name')
    images_entry = Images.objects(college=college).first()

    if not images_entry:
        return render(request, 'category_images.html', {'error': 'No images found'})

    category_map = {
        'classroom': images_entry.classroom or [],
        'lab': images_entry.lab or [],
        'canteen': images_entry.canteen or [],
        'pwd': images_entry.pwd or [],
        'parking': images_entry.parking or [],
        'washroom': images_entry.washroom or [],
    }

    raw_images = category_map.get(category, [])
    
    # Extract URLs from complex objects
    # classroom/lab have structure: {url: [url1, url2, ...], ...}
    # canteen/pwd/parking/washroom are just URL strings
    image_urls = []
    for item in raw_images:
        if isinstance(item, str):
            # Direct URL string (canteen, pwd, parking, washroom)
            if item:
                image_urls.append(item)
        elif isinstance(item, dict) and 'url' in item:
            # Object with url field (classroom, lab)
            url_data = item['url']
            if isinstance(url_data, list) and len(url_data) > 0:
                # Extract first URL from list
                image_urls.append(url_data[0])
            elif isinstance(url_data, str):
                image_urls.append(url_data)

    return render(
        request,
        'category_images.html',
        {
            'image_urls': image_urls,
            'category': category
        }
    )


def get_category_images_json(request, category):
    """API endpoint to fetch images in JSON format"""
    try:
        college = request.session.get('college')
        
        if not college:
            return JsonResponse({'error': 'Not authenticated', 'image_urls': []}, status=401)
        
        images_entry = Images.objects(college_name=college).first()
        
        if not images_entry:
            return JsonResponse({'error': 'No images found', 'image_urls': []}, status=404)
        
        category_map = {
            'classroom': images_entry.classroom or [],
            'lab': images_entry.lab or [],
            'canteen': images_entry.canteen or [],
            'pwd': images_entry.pwd or [],
            'parking': images_entry.parking or [],
            'washroom': images_entry.washroom or [],
        }
        
        raw_images = category_map.get(category, [])
        
        # Extract URLs from complex objects
        # classroom/lab have structure: {url: [url1, url2, ...], ...}
        # canteen/pwd/parking/washroom are just URL strings
        image_urls = []
        for item in raw_images:
            if isinstance(item, str):
                # Direct URL string (canteen, pwd, parking, washroom)
                if item:
                    image_urls.append(item)
            elif isinstance(item, dict) and 'url' in item:
                # Object with url field (classroom, lab)
                url_data = item['url']
                if isinstance(url_data, list) and len(url_data) > 0:
                    # Extract first URL from list
                    image_urls.append(url_data[0])
                elif isinstance(url_data, str):
                    image_urls.append(url_data)
        
        print(f"[get_category_images_json] Category: {category}, College: {college}, Found {len(image_urls)} images")
        logger.info("get_category_images_json: category=%s, college=%s, count=%d", category, college, len(image_urls))
        
        return JsonResponse({
            'category': category,
            'image_urls': image_urls,
            'count': len(image_urls)
        })
    
    except Exception as e:
        print(f"[get_category_images_json] Error: {e}")
        logger.error("get_category_images_json error: %s", e)
        return JsonResponse({'error': str(e), 'image_urls': []}, status=500)
