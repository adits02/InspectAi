from django.shortcuts import render, redirect
from django.contrib import messages
from django.http import JsonResponse, FileResponse, Http404
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt

from mongoengine import DoesNotExist

from .models import certificate, mandatory_dis, supporting_document, College, Images, InspectionRequest


# --------------------------------------------------
# HELPER CLASSES
# --------------------------------------------------
from io import BytesIO

class SafeFileWrapper:
    """
    Reliable wrapper for file content that works with mongoengine FileField.
    Handles position tracking and ensures full content is always readable.
    """
    def __init__(self, content: bytes):
        self.content = content if isinstance(content, bytes) else content.encode('utf-8') if isinstance(content, str) else bytes(content)
        self.pos = 0
    
    def read(self, size=-1):
        """Read data from the current position"""
        if size is None or size < 0:
            # Read all remaining data
            data = self.content[self.pos:]
            self.pos = len(self.content)
        else:
            # Read up to size bytes
            data = self.content[self.pos:self.pos + size]
            self.pos += len(data)
        return data
    
    def seek(self, pos, whence=0):
        """Seek to a position in the file"""
        if whence == 0:  # Absolute position
            self.pos = pos
        elif whence == 1:  # Relative to current position
            self.pos += pos
        elif whence == 2:  # Relative to end
            self.pos = len(self.content) + pos
        self.pos = max(0, min(self.pos, len(self.content)))
        return self.pos
    
    def tell(self):
        """Return current position"""
        return self.pos
    
    def close(self):
        """No-op for in-memory content"""
        pass
    
    def __enter__(self):
        return self
    
    def __exit__(self, *args):
        self.close()


# --------------------------------------------------
# COLLEGE SIGNUP
# --------------------------------------------------
from inspector.models import Feedback

import requests
import cloudinary.uploader
import threading


# --------------------------------------------------
# COLLEGE SIGNUP
# --------------------------------------------------
def signup_view(request):
    if request.method == 'POST':
        college_name = request.POST.get('college_name')
        college_id = request.POST.get('college_id')
        pin_id = request.POST.get('pin_id')
        email = request.POST.get('email')
        state = request.POST.get('state')
        city = request.POST.get('city')
        password = request.POST.get('password')
        confirm_password = request.POST.get('confirm_password')

        if College.objects(college_id=college_id).first():
            messages.error(request, 'College already exists.')
            return redirect('signup')

        if password != confirm_password:
            messages.error(request, 'Passwords do not match.')
            return redirect('signup')

        if College.objects(email=email).first():
            messages.error(request, 'Email already exists.')
            return redirect('signup')

        college = College(
            college_name=college_name,
            college_id=college_id,
            pin_id=pin_id,
            email=email,
            state=state,
            city=city,
            password=password,
            approved="Approved"
        )
        college.save()

        messages.success(request, 'Signup successful. Please login.')
        return redirect('college_login')

    return render(request, 'signup.html')


# --------------------------------------------------
# COLLEGE LOGIN
# --------------------------------------------------
def login_view(request):
    if request.method == 'POST':
        college_name = request.POST.get('college_name')
        college_id = request.POST.get('college_code')
        password = request.POST.get('password')

        try:
            college = College.objects.get(
                college_name=college_name,
                college_id=college_id,
                password=password
            )

            request.session['college_name'] = college.college_name
            return redirect('index')

        except DoesNotExist:
            messages.error(request, 'Invalid credentials.')

    return render(request, 'college_login.html')


# --------------------------------------------------
# UPLOAD MANDATORY DISCLOSURE
# --------------------------------------------------
def upload_mandatory_dis(request):
    if request.method == 'POST':
        if 'mandatory_doc' not in request.FILES:
            messages.error(request, "No file uploaded.")
            return redirect('upload_excel')

        college_name = request.session.get('college_name')
        if not college_name:
            messages.error(request, "Session expired or not logged in; please login again.")
            return redirect('college_login')

        college_intake = request.POST.get("college_intake")
        if not college_intake:
            messages.error(request, "College intake is required.")
            return redirect('upload_excel')

        # Mandatory Disclosure PDF
        mandatory_file = request.FILES.get('mandatory_doc')
        if not mandatory_file:
            messages.error(request, "No mandatory disclosure file uploaded.")
            return redirect('upload_excel')

        mandatory_entry = mandatory_dis(
            name="Mandatory Disclosure",
            file=mandatory_file,
            college_name=college_name,
            college_intake=college_intake
        )
        mandatory_entry.save()

        # Additional required docs (best effort for doc completeness)
        extra_files = {
            'faculty_qualification': "Faculty Qualification Document",
            'faculty_experience': "Faculty Experience Document",
            'student_admission': "Student Admission / Student Strength Document",
            'approval_affiliation': "Approval / Affiliation Letter",
            'fire_noc': "Fire NOC / Safety Certificate"
        }

        for field, doc_name in extra_files.items():
            uploaded = request.FILES.get(field)
            if uploaded:
                sup_doc = supporting_document(
                    name=doc_name,
                    file=uploaded,
                    college_name=college_name,
                    field_name=field,
                    verified='Pending'
                )
                sup_doc.save()

        document_summary = None
        faculty_validation = None
        student_faculty_ratio = None
        approval_certificate_validity = None

        # Default local completeness check so UI always has something to display
        try:
            from fastapi_app import check_missing_documents
            document_summary = check_missing_documents(college_name, college_intake)
        except Exception as e:
            document_summary = None
            print(f"Local completeness fallback failed: {e}")

        try:
            process_resp = requests.post(
                "http://localhost:8001/process-mandatory-disclosure/",
                json={"college_name": college_name, "college_intake": college_intake}
            )
            if process_resp.status_code == 200:
                response_data = process_resp.json()
                document_summary = response_data.get('document_completeness', document_summary)
                report_response = response_data.get('report_response', {})
                faculty_validation = report_response.get('faculty_qualification_experience')
                student_faculty_ratio = report_response.get('student_faculty_ratio')
                approval_certificate_validity = report_response.get('approval_certificate_validity')
            else:
                messages.warning(request, f'Compliance processing returned status {process_resp.status_code}. Showing local completeness.')

            # Generate compliance report automatically in background thread (non-blocking)
            print(f"\n[Auto-Compliance] Queuing compliance report generation in background...")
            report_thread = threading.Thread(
                target=generate_compliance_report_background,
                args=(college_name, college_intake),
                daemon=True
            )
            report_thread.start()

        except Exception as e:
            messages.warning(request, f'Uploaded successfully, but report processing failed: {str(e)}')

        messages.success(request, 'Mandatory disclosure uploaded. Compliance report generation started in background.')
        return redirect('index')

    # GET request - check for existing uploads
    college_name = request.session.get('college_name')
    existing_mandatory = None
    existing_supporting = {}
    if college_name:
        existing_mandatory = mandatory_dis.objects(college_name=college_name).first()
        for field in ['faculty_qualification', 'faculty_experience', 'student_admission', 'approval_affiliation', 'fire_noc']:
            doc = supporting_document.objects(college_name=college_name, field_name=field).first()
            if doc:
                existing_supporting[field] = doc

    return render(request, 'upload_excel.html', {
        'existing_mandatory': existing_mandatory,
        'existing_supporting': existing_supporting
    })


# --------------------------------------------------
# UPLOAD CERTIFICATES
# --------------------------------------------------
def upload_certificate(request):
    if request.method == 'POST':
        college_name = request.session.get('college_name')
        college_intake = request.POST.get('college_intake') or request.session.get('college_intake')

        certificate_map = {
            'anti_ragging_cert': "Anti-Ragging Committee Certificate",
            'internal_committee_cert': "Internal Committee Certificate",
            'annual_ic_report': "Annual IC Report",
            'scst_committee_cert': "SC/ST Committee Certificate",
            'iic_cert': "Institution’s Innovation Council (IIC) Certificate",
            'abc_cert': "Academic Bank of Credit (ABC) Compliance",
            'digital_transactions_cert': "Digital Transactions Certificate",
            'mental_health_cert': "Mental Health Counselling Center Certificate",
            'internal_assessment_cert': "Internal Assessment and Laboratory Work Compliance Certificate",
            'fire_safety_cert': "Fire and Life Safety Certificate",
            'occupancy_cert': "Approved Plan and Occupancy Certificate",
            'financial_statement_cert': "Audited Financial Statement",
            'advocate_cert': "Certificate of Advocate",
            'architect_cert': "Certificate of Architect Registered with Council of Architecture",
            'bank_manager_cert': "Certificate of the Bank Manager",
            'incorporation_cert': "Certificate of Incorporation",
            'building_cert': "Occupancy/Completion/Building License Certificate/Form D",
            'minority_status_cert': "Certificate Regarding Minority Status",
            'architect_details_cert': "Certificate by an Architect",
            'structural_stability_cert': "Structural Stability Certificate",
            'institute_undertaking': "Undertaking by the Institute",
        }

        # Initialize verifier
        from final_certificate_verification import CertificateVerifier
        import tempfile, os
        from datetime import datetime

        verifier = CertificateVerifier()

        for field, cert_name in certificate_map.items():
            if field in request.FILES:
                uploaded = request.FILES[field]
                print(f"\n[upload_certificates] Processing: {cert_name} (field={field})")

                # Read the uploaded file content into memory (as bytes)
                file_content = b''
                try:
                    for chunk in uploaded.chunks():
                        file_content += chunk
                except Exception as chunk_err:
                    print(f"[upload_certificates] ❌ Error reading chunks: {chunk_err}")
                    continue
                
                if not file_content:
                    print(f"[upload_certificates] ⚠️ Empty file uploaded for {cert_name}")
                    continue
                
                print(f"[upload_certificates] ✅ Read {len(file_content)} bytes for {cert_name}")

                # Save uploaded file to a temp file for verification
                suffix = os.path.splitext(uploaded.name)[1] or '.pdf'
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                    tmp.write(file_content)
                    tmp_path = tmp.name
                    print(f"[upload_certificates] ✅ Wrote {len(file_content)} bytes to temp: {tmp_path}")

                # Run non-interactive verification
                try:
                    results = verifier.process_certificate(tmp_path, metadata_words=[college_name], interactive=False)
                except Exception as e:
                    print(f"[upload_certificates] ⚠️ Verification failed: {str(e)[:100]}")
                    results = {
                        'text_extraction': False,
                        'certificate_type': None,
                        'format_match': False,
                        'format_details': None
                    }

                # Create certificate document with verification results
                # Use SafeFileWrapper for reliable GridFS storage
                file_obj = SafeFileWrapper(file_content)
                
                print(f"[upload_certificates] Creating certificate document...")
                print(f"[upload_certificates] File wrapper size: {len(file_content)} bytes")
                cert_doc = certificate(
                    name=cert_name,
                    file=file_obj,
                    college_name=college_name,
                    field_name=field,
                    verified=('Verified' if results.get('format_match') else 'Rejected'),
                    verified_by='system',
                    verified_at=str(datetime.utcnow()),
                    score=str(results.get('format_details', {}).get('overall_similarity')) if results.get('format_details') else '',
                    notes='' if results.get('format_match') else 'Format mismatch or OCR failed',
                    format_details=results.get('format_details') or {}
                )
                
                try:
                    cert_doc.save()
                    print(f"[upload_certificates] ✅ Certificate document saved to DB")
                    print(f"[upload_certificates]    ID: {cert_doc.id}")
                    print(f"[upload_certificates]    File obj type: {type(cert_doc.file)}")
                    print(f"[upload_certificates]    Size: {len(file_content)} bytes")
                    
                    # CRITICAL: Verify the file was actually saved to GridFS
                    try:
                        saved_cert = certificate.objects.get(id=cert_doc.id)
                        print(f"[upload_certificates] ✅ Refetched from DB - ID: {saved_cert.id}")
                        print(f"[upload_certificates]    Saved file type: {type(saved_cert.file)}")
                        print(f"[upload_certificates]    Has read method: {hasattr(saved_cert.file, 'read')}")
                        print(f"[upload_certificates]    Has _file attr: {hasattr(saved_cert.file, '_file')}")
                        
                        # Try to read it back
                        if hasattr(saved_cert.file, 'seek'):
                            saved_cert.file.seek(0)
                        
                        test_read = None
                        try:
                            test_read = saved_cert.file.read()
                            print(f"[upload_certificates] ✅ Successfully read from GridFS: {len(test_read) if test_read else 0} bytes")
                        except Exception as read_err:
                            print(f"[upload_certificates] ⚠️ Failed to read from saved file: {read_err}")
                        
                        if test_read and len(test_read) > 0:
                            print(f"[upload_certificates] ✅✅ VERIFIED: File is properly stored in GridFS")
                        else:
                            print(f"[upload_certificates] ❌ PROBLEM: GridFS read returned empty data")
                            print(f"[upload_certificates]    Original size: {len(file_content)} bytes")
                            print(f"[upload_certificates]    GridFS read: {len(test_read) if test_read else 0} bytes")
                    except Exception as verify_err:
                        print(f"[upload_certificates] ⚠️ Could not verify save: {verify_err}")
                    
                except Exception as save_err:
                    print(f"[upload_certificates] ❌ Error saving certificate {cert_name}: {save_err}")
                    import traceback
                    traceback.print_exc()

                # Cleanup temp file
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
                    
                print(f"[upload_certificates] Finished processing: {cert_name}\n")

        # Generate compliance report automatically in background thread after certificates are uploaded
        if college_intake:
            print(f"\n[Auto-Compliance] Queuing compliance report generation after certificate upload...")
            report_thread = threading.Thread(
                target=generate_compliance_report_background,
                args=(college_name, college_intake),
                daemon=True
            )
            report_thread.start()

        messages.success(request, "Certificates uploaded successfully. Compliance report generation started in background.")
        return redirect('index')

    return redirect('upload_certificate')


# --------------------------------------------------
# VIEW FEEDBACK
# --------------------------------------------------
def view_feedback(request):
    college_name = request.session.get('college_name')
    feedback_entry = Feedback.objects.all()

    return render(
        request,
        'inspector/feedback_view.html',
        {
            'feedback_entry': feedback_entry,
            'college_name': college_name
        }
    )


# --------------------------------------------------
# DOWNLOAD MANUAL REPORT
# --------------------------------------------------
def download_manual_report(request, feedback_id):
    try:
        feedback = Feedback.objects.get(id=feedback_id)
        return FileResponse(
            feedback.manual_report,
            as_attachment=True,
            filename=f"{feedback.college_name}_report.pdf"
        )
    except DoesNotExist:
        raise Http404("Report not found")


# --------------------------------------------------
# COLLEGE LOGOUT
# --------------------------------------------------
def college_logout(request):
    request.session.flush()
    return render(request, 'options.html')


# --------------------------------------------------
# INSPECTION REQUEST
# --------------------------------------------------
def request_inspection(request):
    college_name = request.session.get('college_name')
    if not college_name:
        return redirect('college_login')

    # Check if college already has an existing inspection request
    existing_request = InspectionRequest.objects(college_name=college_name).first()
    
    if existing_request:
        # Show inspection status dashboard
        return render(request, 'institute/inspection_status.html', {
            'request': existing_request,
            'college_name': college_name
        })

    if request.method == 'POST':
        request_reason = request.POST.get('request_reason')
        preferred_date = request.POST.get('preferred_date')

        inspection_request = InspectionRequest(
            college_name=college_name,
            request_reason=request_reason,
            preferred_date=preferred_date
        )
        inspection_request.save()

        messages.success(request, "Inspection request submitted successfully.")
        return redirect('request_inspection')

    return render(request, 'institute/request_inspection.html', {'college_name': college_name})


# --------------------------------------------------
# IMAGE UPLOAD (CLOUDINARY)
# --------------------------------------------------
def generate_deficiency_report_background(college_name, branch):
    """
    Generate deficiency report in background thread after images are uploaded.
    Does not block the main upload response.
    """
    try:
        print(f"[Auto-Report] Starting background report generation for {college_name} ({branch})")
        
        fastapi_url = "http://localhost:8001/generate-report/"
        payload = {
            "college_name": college_name,
            "branch": branch
        }
        
        response = requests.post(fastapi_url, json=payload, timeout=120)
        
        if response.status_code == 200:
            result = response.json()
            file_id = result.get('file_id')
            inspection_scores = result.get('inspection_scores', {})
            final_status = inspection_scores.get('final_overall_status', 'Unknown')
            final_score = inspection_scores.get('final_overall_score', 0)
            
            print(f"[Auto-Report] ✅ Report generated successfully")
            print(f"[Auto-Report] Report ID: {file_id}")
            print(f"[Auto-Report] Status: {final_status} | Score: {final_score}%")
        else:
            error_msg = response.json().get('detail', f'Status {response.status_code}')
            print(f"[Auto-Report] ❌ Failed: {error_msg}")
    
    except requests.exceptions.Timeout:
        print(f"[Auto-Report] ❌ Timeout: Report generation took too long")
    except requests.exceptions.ConnectionError:
        print(f"[Auto-Report] ⚠️ FastAPI server not running on port 8001")
    except Exception as e:
        print(f"[Auto-Report] ❌ Unexpected error: {str(e)}")
        import traceback
        traceback.print_exc()


def generate_compliance_report_background(college_name, college_intake):
    """
    Generate compliance report in background thread after documents are uploaded.
    Does not block the main upload response.
    """
    try:
        print(f"\n[Auto-Compliance] Starting background compliance report generation")
        print(f"[Auto-Compliance] College: {college_name}, Intake: {college_intake}")
        print(f"[Auto-Compliance] Timestamp: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        fastapi_url = "http://localhost:8001/create-compliance-report/"
        payload = {
            "college_name": college_name,
            "college_intake": college_intake
        }
        
        response = requests.post(fastapi_url, json=payload, timeout=180)
        
        if response.status_code == 200:
            result = response.json()
            file_id = result.get('file_id')
            final_score = result.get('final_compliance_score', 0)
            overall_status = result.get('overall_compliance_status', 'Unknown')
            
            print(f"[Auto-Compliance] ✅ Compliance report generated successfully")
            print(f"[Auto-Compliance] Report ID: {file_id}")
            print(f"[Auto-Compliance] Overall Status: {overall_status} | Score: {final_score}%")
            print(f"[Auto-Compliance] ==================================================\n")
        else:
            try:
                error_msg = response.json().get('detail', f'Status {response.status_code}')
            except:
                error_msg = f'HTTP {response.status_code}'
            print(f"[Auto-Compliance] ❌ Failed to generate report: {error_msg}")
    
    except requests.exceptions.Timeout:
        print(f"[Auto-Compliance] ❌ Timeout: Compliance report generation took too long (>180s)")
    except requests.exceptions.ConnectionError:
        print(f"[Auto-Compliance] ⚠️ FastAPI server not running on port 8001 - skipping auto-generation")
    except Exception as e:
        print(f"[Auto-Compliance] ❌ Unexpected error: {str(e)}")
        import traceback
        traceback.print_exc()


@csrf_exempt
def u_i(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid method'}, status=405)

    route = request.POST.get('route').rstrip('/')
    branch = request.POST.get('branch')
    itbk = request.POST.get('itbk')
    nod = request.POST.get('nod')
    nob = request.POST.get('nob')

    route_map = {
        '/institute/classroom_upload': 'classroom',
        '/classroom_upload': 'classroom',
        '/institute/lab_upload': 'lab',
        '/lab_upload': 'lab',
        '/institute/canteen_upload': 'canteen',
        '/canteen_upload': 'canteen',
        '/institute/pwd_upload': 'pwd',
        '/pwd_upload': 'pwd',
        '/institute/parking_upload': 'parking',
        '/parking_upload': 'parking',
        '/institute/washroom_upload': 'washroom',
        '/washroom_upload': 'washroom',
    }

    college_name = request.session.get('college_name')
    if not college_name:
        return JsonResponse({'error': 'Session expired. Please login again.'}, status=401)

    uploaded_files = request.FILES.getlist('image')
    if not uploaded_files:
        return JsonResponse({'error': 'No files uploaded'}, status=400)

    target_field = route_map.get(route)
    if not target_field:
        return JsonResponse({'error': 'Invalid route'}, status=400)

    images_entry = Images.objects(college=college_name).first() or Images(college=college_name)

    if not hasattr(images_entry, target_field):
        setattr(images_entry, target_field, [])

    data = {
        'branch': branch,
        'itbk': itbk,
        'nod': nod,
        'nob': nob,
        'url': []
    }

    for file in uploaded_files:
        result = cloudinary.uploader.upload(file)
        data['url'].append(result['url'])

    if target_field in ['classroom', 'lab']:
        getattr(images_entry, target_field).append(data)
    else:
        getattr(images_entry, target_field).extend(data['url'])
    images_entry.save()

    # ✅ AUTO-GENERATE COMPREHENSIVE INSPECTION REPORT IN BACKGROUND
    # Check if core images (classroom + lab) exist - these are required
    has_classroom = images_entry.classroom and len(images_entry.classroom) > 0
    has_lab = images_entry.lab and len(images_entry.lab) > 0
    has_canteen = hasattr(images_entry, 'canteen') and images_entry.canteen and len(images_entry.canteen) > 0
    has_pwd = hasattr(images_entry, 'pwd') and images_entry.pwd and len(images_entry.pwd) > 0
    has_parking = hasattr(images_entry, 'parking') and images_entry.parking and len(images_entry.parking) > 0
    has_washroom = hasattr(images_entry, 'washroom') and images_entry.washroom and len(images_entry.washroom) > 0
    
    if has_classroom and has_lab:
        print(f"[Image Upload] Core images found for {college_name}")
        print(f"[Image Upload] Classroom: ✓, Lab: ✓, Canteen: {'✓' if has_canteen else '-'}, PWD: {'✓' if has_pwd else '-'}, Parking: {'✓' if has_parking else '-'}, Washroom: {'✓' if has_washroom else '-'}")
        print(f"[Image Upload] Triggering automatic comprehensive inspection report generation...")
        
        # Start background thread to generate report
        report_thread = threading.Thread(
            target=generate_deficiency_report_background,
            args=(college_name, branch),
            daemon=True
        )
        report_thread.start()
    else:
        print(f"[Image Upload] Waiting for core images (classroom + lab)...")
        print(f"[Image Upload] Classroom: {has_classroom}, Lab: {has_lab}")

    return JsonResponse({
        'message': 'Images uploaded successfully',
        'auto_report': 'Generating comprehensive report in background...' if (has_classroom and has_lab) else 'Waiting for all required images'
    })


# --------------------------------------------------
# GENERATE DEFICIENCY REPORT FROM UPLOADED IMAGES
# --------------------------------------------------
def generate_deficiency_report(request):
    """
    Generate deficiency report from uploaded classroom/lab images.
    GET: Display form to select branch for report generation
    POST: Trigger report generation and download PDF
    """
    college_name = request.session.get('college_name')
    if not college_name:
        messages.error(request, "Session expired. Please login again.")
        return redirect('college_login')

    if request.method == 'GET':
        # Display form to select branch
        return render(request, 'institute/generate_deficiency_report.html', {
            'college_name': college_name
        })
    
    elif request.method == 'POST':
        branch = request.POST.get('branch', 'entc').strip()
        
        if not branch:
            messages.error(request, "Branch is required to generate report.")
            return redirect('generate_deficiency_report')
        
        try:
            # Check if images exist for this college
            images_doc = Images.objects(college=college_name).first()
            if not images_doc:
                messages.error(request, "No images uploaded for this college yet.")
                return redirect('generate_deficiency_report')
            
            has_classroom = images_doc.classroom and len(images_doc.classroom) > 0
            has_lab = images_doc.lab and len(images_doc.lab) > 0
            
            if not has_classroom or not has_lab:
                messages.error(request, "Both classroom and lab images are required to generate a deficiency report.")
                return redirect('generate_deficiency_report')
            
            # Call FastAPI endpoint to generate report
            fastapi_url = "http://localhost:8001/generate-report/"
            payload = {
                "college_name": college_name,
                "branch": branch
            }
            
            print(f"[Deficiency Report] Calling FastAPI endpoint: {fastapi_url}")
            print(f"[Deficiency Report] Payload: {payload}")
            
            response = requests.post(fastapi_url, json=payload, timeout=120)
            
            if response.status_code == 200:
                result = response.json()
                file_id = result.get('file_id')
                inspection_scores = result.get('inspection_scores', {})
                
                # Retrieve PDF from MongoDB
                from yolo_classroom import deficiency_report
                report_doc = deficiency_report.objects(id=file_id).first()
                
                if report_doc and report_doc.file:
                    # Return PDF as download
                    pdf_filename = f"{college_name}_{branch}_deficiency_report.pdf"
                    return FileResponse(
                        report_doc.file,
                        as_attachment=True,
                        filename=pdf_filename,
                        media_type='application/pdf'
                    )
                else:
                    messages.error(request, "Report was generated but could not be retrieved.")
                    return redirect('generate_deficiency_report')
            
            elif response.status_code == 400:
                error_msg = response.json().get('detail', 'Invalid request parameters.')
                messages.error(request, f"Report generation failed: {error_msg}")
                return redirect('generate_deficiency_report')
            
            elif response.status_code == 404:
                messages.error(request, "No images found for this college and branch.")
                return redirect('generate_deficiency_report')
            
            else:
                error_msg = response.json().get('detail', f'Server error: {response.status_code}')
                messages.error(request, f"Report generation failed: {error_msg}")
                return redirect('generate_deficiency_report')
        
        except requests.exceptions.Timeout:
            messages.error(request, "Report generation took too long. Please try again.")
            print("[Deficiency Report] FastAPI request timed out")
            return redirect('generate_deficiency_report')
        
        except requests.exceptions.ConnectionError:
            messages.error(request, "Cannot connect to report generation service. Ensure FastAPI server is running.")
            print("[Deficiency Report] FastAPI connection error")
            return redirect('generate_deficiency_report')
        
        except Exception as e:
            messages.error(request, f"Unexpected error: {str(e)}")
            print(f"[Deficiency Report] Error: {e}")
            import traceback
            traceback.print_exc()
            return redirect('generate_deficiency_report')


# --------------------------------------------------
# CHECK/AUTO-GENERATE REPORT (API ENDPOINT)
# --------------------------------------------------
@csrf_exempt
def check_and_generate_report(request):
    """
    API endpoint to check if report exists for uploaded images.
    If report doesn't exist, generate it automatically.
    Used by frontend to trigger auto-generation.
    """
    try:
        college_name = request.session.get('college_name')
        if not college_name:
            return JsonResponse({'error': 'Not logged in'}, status=401)
        
        branch = request.POST.get('branch', 'entc')
        
        # Check if images exist
        images_doc = Images.objects(college=college_name).first()
        if not images_doc:
            return JsonResponse({'error': 'No images uploaded'}, status=404)
        
        has_classroom = images_doc.classroom and len(images_doc.classroom) > 0
        has_lab = images_doc.lab and len(images_doc.lab) > 0
        
        if not has_classroom or not has_lab:
            return JsonResponse({'error': 'Both classroom and lab images required'}, status=400)
        
        # Check if report already exists
        from yolo_classroom import deficiency_report
        existing_report = deficiency_report.objects(college=college_name, branch=branch).first()
        
        if existing_report:
            return JsonResponse({
                'status': 'exists',
                'message': 'Report already generated',
                'file_id': str(existing_report.id)
            })
        
        # Trigger report generation in background
        report_thread = threading.Thread(
            target=generate_deficiency_report_background,
            args=(college_name, branch),
            daemon=True
        )
        report_thread.start()
        
        return JsonResponse({
            'status': 'generating',
            'message': 'Report generation started in background'
        })
    
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
