from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from mongoengine import connect, Document, StringField, FileField
import os
import pdfplumber
import math
import pandas as pd
from io import BytesIO
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from yolo_classroom import (
    get_image_evidence_summary, 
    calculate_image_inspection_score,
    process_classroom_images,
    process_lab_images,
    process_canteen_images,
    process_pwd_images,
    process_parking_images,
    process_washroom_images,
    calculate_dynamic_thresholds,
    generate_pdf,
    get_cloudinary_image_as_binary,
    deficiency_report
)
import google.generativeai as genai
import traceback
import re
import requests

# Configure Gemini API
genai.configure(api_key="AIzaSyAogPEvYUJLJokjsV0oz1zl3_L81BKTcAY")
from institute.models import Images

app = FastAPI()


def safe_read_file_field(file_field):
    """Safely read a mongoengine FileField or BinaryField."""
    try:
        if file_field is None:
            return None
        
        from bson import ObjectId
        
        # If it's already bytes (from BinaryField), return directly
        if isinstance(file_field, bytes):
            return file_field
        
        # If it's an ObjectId (GridFS reference from FileField), try to read it
        if isinstance(file_field, ObjectId):
            print(f"File stored as GridFS ObjectId: {file_field}")
            if hasattr(file_field, 'read'):
                return file_field.read()
            return None
        
        # If it's a file-like object with read method, read it
        if hasattr(file_field, 'read'):
            return file_field.read()
        
        # If it's a string, encode it
        if isinstance(file_field, str):
            return file_field.encode('utf-8')
        
        # Otherwise return as-is and let caller handle
        return file_field
    except Exception as e:
        print(f"Error reading file field: {e}")
        raise


def check_missing_documents(college_name: str, college_intake: str = None):
    """Detect presence/missing status for required compliance documents."""
    from institute.models import mandatory_dis, certificate, supporting_document

    required = [
        ("Mandatory Disclosure PDF", "mandatory_disclosure", "Critical"),
        ("Faculty Qualification Document", "faculty_qualification", "High"),
        ("Faculty Experience Document", "faculty_experience", "High"),
        ("Student Admission / Student Strength Document", "student_admission", "High"),
        ("Approval / Affiliation Letter", "approval_affiliation", "Medium"),
        ("Fire NOC / Safety Certificate", "fire_noc", "High"),
        ("NAAC Accreditation Certificate", "naac_accreditation", "High"),
        ("Certificate Regarding Minority Status", "minority_status", "High"),
        ("Structural Stability Certificate", "structural_stability", "High"),
    ]

    status_list = []
    found_count = 0

    # Check mandatory_dis directly (primary source)
    try:
        mand = mandatory_dis.objects(college_name=college_name, college_intake=college_intake).first()
        has_mandatory = bool(mand)
    except Exception:
        has_mandatory = False

    # Query known certificates (placeholder and real if available)
    try:
        certs = list(certificate.objects(college_name=college_name))
    except Exception:
        certs = []

    # Query supporting docs uploaded via institute upload form
    try:
        supp_docs = list(supporting_document.objects(college_name=college_name))
    except Exception:
        supp_docs = []

    def doc_exists(keywords, doc_key=None):
        if has_mandatory and "mandatory_disclosure" in keywords:
            return True

        # If there is a direct key match for supporting_document field_name, prefer it
        if doc_key:
            for sup in supp_docs:
                if hasattr(sup, "field_name") and (sup.field_name or "").lower() == doc_key:
                    return True

        for cert in certs:
            field_name = (getattr(cert, "field_name", "") or "").lower()
            name = (getattr(cert, "name", "") or "").lower()
            if any(k in field_name or k in name for k in keywords):
                return True

        for sup in supp_docs:
            field_name = (getattr(sup, "field_name", "") or "").lower()
            name = (getattr(sup, "name", "") or "").lower()
            if any(k in field_name or k in name for k in keywords):
                return True

        return False

    for doc_label, doc_key, severity in required:
        present = False
        if doc_key == "mandatory_disclosure":
            present = has_mandatory
        elif doc_key == "faculty_qualification":
            present = doc_exists(["qualification", "faculty qualification", "faculty_qual"], doc_key)
        elif doc_key == "faculty_experience":
            present = doc_exists(["experience", "faculty experience", "fac_experience"], doc_key)
        elif doc_key == "student_admission":
            present = doc_exists(["admission", "student strength", "student_strength"], doc_key)
        elif doc_key == "approval_affiliation":
            present = doc_exists(["approval", "affiliation", "aicte approval", "university affiliation"], doc_key)
        elif doc_key == "fire_noc":
            present = doc_exists(["fire", "noc", "safety certificate", "safety"], doc_key)
        elif doc_key == "naac_accreditation":
            present = doc_exists(["naac", "accreditation", "grade", "assessment", "re-accreditation"], doc_key)
        elif doc_key == "minority_status":
            present = doc_exists(["minority", "minority status", "minority certificate"], doc_key)
        elif doc_key == "structural_stability":
            present = doc_exists(["structural stability", "structural", "stability", "engineer"], doc_key)

        if present:
            status = "Present"
            found_count += 1
        else:
            status = "Missing"

        status_list.append({
            "document_name": doc_label,
            "status": status,
            "severity": severity,
        })

    total = len(required)
    completeness = round((found_count / total) * 100, 2) if total > 0 else 0.0

    return {
        "completeness_percentage": completeness,
        "present_count": found_count,
        "required_count": total,
        "documents": status_list,
    }


def build_certificate_document_crosscheck(college_name: str, college_intake: str = None):
    """Cross-check declared compliance documents vs actual certificate PDFs."""
    import tempfile
    from final_certificate_verification import CertificateVerifier
    from institute.models import certificate
    from fuzzywuzzy import fuzz

    declared_docs = check_missing_documents(college_name, college_intake)

    verifier = CertificateVerifier()
    certificates = list(certificate.objects(college_name=college_name))

    certificate_mapping = []
    for cert in certificates:
        raw_text = ''
        certificate_mapping.append({
            'name': getattr(cert, 'name', ''),
            'field_name': getattr(cert, 'field_name', ''),
            'verified': getattr(cert, 'verified', ''),
            'id': str(getattr(cert, 'id', ''))
        })

    with tempfile.TemporaryDirectory() as temp_dir:
        for cert in certificates:
            try:
                cert_bytes = safe_read_file_field(cert.file)
                if not cert_bytes:
                    continue
                infer_name = (cert.field_name or cert.name or 'certificate').replace(' ', '_').replace('/', '_')
                filename = f"{str(cert.id)}_{infer_name}.pdf"
                file_path = os.path.join(temp_dir, filename)
                with open(file_path, 'wb') as f:
                    f.write(cert_bytes)
            except Exception as e:
                print(f"Error exporting cert {getattr(cert, 'id', '<unknown>')}: {e}")

        verification_report = verifier.validate_required_certificates_in_dir(temp_dir, metadata_fields={'institute_name': college_name})

    # build declared vs actual mapping
    declared_names = [d.get('document_name', '').lower() for d in declared_docs.get('documents', [])]
    scanned_report = verification_report.get('certificates', {})

    document_vs_pdf_matching = []
    mismatch_flags = []

    # Map each declared document to best scanned certificate if name similarity matches
    for declared_doc in declared_docs.get('documents', []):
        declared_name = declared_doc.get('document_name', '')
        declared_status = declared_doc.get('status', 'Unknown')

        best_match = None
        best_score = 0
        for req_name, req_data in scanned_report.items():
            score = fuzz.partial_ratio(declared_name.lower(), req_name.lower())
            if score > best_score:
                best_score = score
                best_match = {'required_name': req_name, 'match_score': score, **req_data}

        matched = best_match and best_match.get('found')
        if declared_status == 'Present' and not matched:
            mismatch_flags.append(f"Declared document '{declared_name}' is marked present but no likely PDF match found.")
        if declared_status == 'Missing' and matched:
            mismatch_flags.append(f"Declared document '{declared_name}' is marked missing but a likely matching PDF exists ({best_match.get('required_name')}).")

        document_vs_pdf_matching.append({
            'declared_name': declared_name,
            'declared_status': declared_status,
            'matched_required_name': best_match.get('required_name') if best_match else None,
            'match_score': best_match.get('match_score') if best_match else None,
            'found_in_pdf': bool(best_match and best_match.get('found')),
            'best_match_status': best_match.get('status') if best_match else 'N/A',
            'best_match_final_status': best_match.get('final_status') if best_match else None
        })

    # Additional key checks
    key_categories = {
        'Fire and Life Safety Certificate': ['fire', 'noc', 'safety'],
        'Approved Plan and Occupancy Certificate': ['occupancy', 'plan', 'building'],
        'NAAC Accreditation Certificate': ['naac', 'accreditation'],
        'Certificate Regarding Minority Status': ['minority'],
        'Structural Stability Certificate': ['structural stability', 'structural']
    }

    key_document_status = {}
    for key_name, keywords in key_categories.items():
        declared_entry = next((d for d in declared_docs.get('documents', []) if d.get('document_name', '').lower() == key_name.lower()), None)
        declared_present = declared_entry and declared_entry.get('status') == 'Present'

        found_in_scanner = any(
            f"{k}" in req_name.lower() for req_name in scanned_report.keys() for k in keywords
        )

        key_document_status[key_name] = {
            'declared_present': declared_present,
            'scanner_detected': found_in_scanner,
            'evidence_status': 'sufficient' if declared_present and found_in_scanner else ('partial' if declared_present or found_in_scanner else 'missing')
        }

        if declared_present and not found_in_scanner:
            mismatch_flags.append(f"Key document '{key_name}' declared but not detected in scanned certificates.")
        if not declared_present and found_in_scanner:
            mismatch_flags.append(f"Key document '{key_name}' not declared but detected in scanned certificates")

    # Create a recommended overall status
    cross_status = 'Compliant'
    if any('missing' in v['evidence_status'] for v in key_document_status.values()):
        cross_status = 'Non-Compliant'
    elif any('partial' in v['evidence_status'] for v in key_document_status.values()):
        cross_status = 'Partial'

    # Wrap up summary
    if not mismatch_flags:
        mismatch_flags.append('No major mismatches detected')

    return {
        'declared_documents': declared_docs,
        'certificate_verification': verification_report,
        'document_vs_pdf_mapping': document_vs_pdf_matching,
        'key_document_status': key_document_status,
        'mismatch_flags': mismatch_flags,
        'overall_cross_validation_status': cross_status
    }


# Connect to MongoDB
try:
    connect(
        db="a13",
        host="mongodb+srv://neolearn02_db_user:3phXJLGvCqwHxtWH@a13.drvtvwx.mongodb.net/a13?retryWrites=true&w=majority",
        tls=True,
        tlsAllowInvalidCertificates=True,
        connect=False
    )
    print("Connected to Atlas MongoDB")
except Exception as e:
    print(f"Failed to connect to Atlas: {e}")
    try:
        connect(db="inspection_system", connect=False)
        print("Connected to local MongoDB")
    except Exception as e2:
        print(f"Failed to connect to local MongoDB: {e2}")


class excel_data(Document):
    college_name = StringField(required=True)
    college_intake = StringField(required=True)
    file_data = FileField()

    meta = {
        'collection': 'excel_data'
    }


class CollegeLoginInfo(BaseModel):
    college_name: str
    college_intake: str


class compliancereport(Document):
    college_name = StringField(required=True)
    intake = StringField(required=False)
    college_intake = StringField(required=False)
    report_file = FileField()
    
    meta = {
        'collection': 'compliance_reports',
        'strict': False
    }


@app.post("/process-mandatory-disclosure/")
async def process_mandatory_disclosure(info: CollegeLoginInfo):
    try:
        print(f"\n{'='*80}")
        print(f"PROCESSING MANDATORY DISCLOSURE")
        print(f"College: {info.college_name}, Intake: {info.college_intake}")
        print(f"{'='*80}\n")
        
        # Import here to avoid circular imports
        from institute.models import mandatory_dis
        
        # Fetch mandatory disclosure
        mandatory_disclosure = mandatory_dis.objects(
            college_name=info.college_name,
            college_intake=info.college_intake
        ).first()
        
        if not mandatory_disclosure:
            print(f"Mandatory disclosure not found for {info.college_name}")
            raise HTTPException(status_code=404, detail="Mandatory disclosure not found")

        # Read the PDF file
        print("Reading PDF file...")
        pdf_file = safe_read_file_field(mandatory_disclosure.file)
        if not pdf_file:
            print("PDF file is empty or None")
            raise HTTPException(status_code=404, detail="PDF file is empty")
        print(f"PDF file size: {len(pdf_file)} bytes")

        # Enhanced keyword mapping for better table identification
        # CRITICAL: Stricter matching to avoid extracting non-compliance tables
        table_keywords = {
            "faculty": {
                "keywords": ["designation", "professor", "qualification", "experience", "name", "department"],
                "title": "Faculty Information",
                    "min_keywords": 4,  # STRICT: Need designation + at least 3 faculty keywords
                "exclude": ["consultancy", "training", "amount", "revenue", "funding", "project", "approved", "intake", "organization", "duration"]
            },
            "classroom": {
                "keywords": ["room type", "classroom", "capacity", "smart", "laboratory"],
                "title": "Classroom Details",
                "min_keywords": 1,  # Relaxed: accept 'room type' header as signal
                "exclude": ["lab", "workshop", "library", "lab information", "consultancy"]
            },
            "smart_classroom": {
                "keywords": ["smart classroom", "smart", "projector", "interactive", "room type"],
                "title": "Smart Classroom Details",
                "min_keywords": 1,  # Relaxed to allow detection when 'smart' or 'smart classroom' appears
                "exclude": ["lab", "laboratory"]
            },
            "laboratory": {
                "keywords": ["laboratory", "lab information", "lab", "equipment", "room type"],
                "title": "Lab Information",
                "min_keywords": 1,  # Relaxed: 'laboratory' header alone is enough
                "exclude": ["classroom", "smart", "consultancy"]
            },
            "workshop": {
                "keywords": ["workshop", "machinery", "tools", "room type"],
                "title": "Workshop Details",
                "min_keywords": 1,  # Relaxed to accept explicit 'workshop' header
                "exclude": ["consultancy"]
            },
            "library": {
                "keywords": ["library", "books", "journals", "titles", "volumes"],
                "title": "Library Details",
                "min_keywords": 1,  # Relaxed to allow detection from header
                "exclude": []
            },
            "intake": {
                "keywords": ["intake", "approved", "sanctioned", "academic year", "student"],
                "title": "Student Intake",
                "min_keywords": 2,  # Relax slightly
                "exclude": ["consultancy"]
            }
        }

        tables_with_titles = []
        
        # Track best table of each type per page to avoid duplicates
        page_best_tables = {}  # {page_num: {category: (df, score)}}

        # Open the PDF and extract tables
        with pdfplumber.open(BytesIO(pdf_file)) as pdf:
            print(f"\nTotal pages in PDF: {len(pdf.pages)}\n")
            
            for page_num, page in enumerate(pdf.pages, start=1):
                try:
                    tables = page.extract_tables()
                    if not tables:
                        continue
                    
                    print(f"Page {page_num}: Found {len(tables)} table(s)")
                    
                    page_tables = {}  # {category: (df, score, table_idx)} for this page
                    
                    for table_idx, table in enumerate(tables):
                        if not table or len(table) < 2:
                            continue
                        
                        # Convert to DataFrame
                        try:
                            df = pd.DataFrame(table)
                        except Exception as e:
                            print(f"  Table {table_idx+1}: Skipping malformed table - {e}")
                            continue
                        
                        # Skip very small tables
                        if df.shape[0] < 2 or df.shape[1] < 2:
                            continue
                        
                        # Get headers for structural analysis
                        headers = [str(x).lower().strip() for x in df.iloc[0]]
                        header_text = ' '.join(headers)

                        # Prepare a snippet of table text for quick classification
                        full_text = ' '.join(
                            str(cell).lower().strip()
                            for row in table[:10] if row
                            for cell in row if cell
                        )

                        # Quick header-based categorization: accept 'Room Type'/'Laboratory'/'Workshop'/'Library' tables
                        if 'room type' in header_text or 'room type' in full_text or 'laboratory' in header_text or 'workshop' in header_text or 'library' in header_text or 'smart' in header_text or 'projector' in header_text:
                            # Decide best category from header/full_text
                            inferred = None
                            if 'laboratory' in header_text or 'laboratory' in full_text or ' lab ' in full_text:
                                inferred = 'laboratory'
                            elif 'workshop' in header_text or 'workshop' in full_text:
                                inferred = 'workshop'
                            elif 'library' in header_text or 'library' in full_text:
                                inferred = 'library'
                            elif 'smart' in header_text or 'projector' in header_text or 'interactive' in header_text or 'smart' in full_text or 'projector' in full_text:
                                inferred = 'smart_classroom'
                            else:
                                inferred = 'classroom'

                            # Keep best match per page; use high score so this takes precedence
                            if inferred not in page_tables or page_tables[inferred][1] < 99:
                                page_tables[inferred] = (df, 99, table_idx)
                                print(f"  Table {table_idx+1}: Quick-matched as '{table_keywords[inferred]['title']}' (header-based)")
                            continue
                        
                        # CRITICAL FIX: Faculty MUST have name, designation, department
                        if "designation" in headers and "name" in headers and "department" in headers:
                            # Verify it's faculty data, not consultancy
                            full_text = ' '.join(
                                str(cell).lower().strip()
                                for row in table[:10] if row
                                for cell in row if cell
                            )
                            
                            # STRICT: Block consultancy/training data
                            if not any(excl in full_text for excl in ["consultancy", "training", "organization", "amount generated", "revenue"]):
                                # This is faculty data
                                matched_category = "faculty"
                                df["Source_Page"] = page_num
                                tables_with_titles.append((df, table_keywords["faculty"]["title"]))
                                print(f"  Table {table_idx+1}: Matched as 'Faculty Information' (strict headers)")
                                continue
                        
                        # Check each category for non-faculty tables
                        matched_category = None
                        max_keyword_matches = 0
                        
                        for category, rules in table_keywords.items():
                            if category == "faculty":
                                continue
                            
                            # IMPROVED: Scan full table (first 10 rows) for exclusions
                            full_text = ' '.join(
                                str(cell).lower().strip()
                                for row in table[:10] if row
                                for cell in row if cell
                            )
                            
                            # First check if any exclude keywords are present
                            if any(excl in full_text for excl in rules["exclude"]):
                                continue
                            
                            # Count keyword matches
                            keyword_matches = sum(1 for kw in rules["keywords"] if kw in (header_text + ' ' + full_text))
                            
                            # Check if meets minimum threshold
                            if keyword_matches >= rules["min_keywords"]:
                                # EXTRA VALIDATION FOR FACULTY: Check if data rows contain professor titles
                                if category == "faculty":
                                    # Scan data rows to verify this is actually faculty data
                                    has_professor_data = False
                                    for data_row_idx in range(1, min(5, df.shape[0])):
                                        row_text = ' '.join([str(x).lower().strip() for x in df.iloc[data_row_idx]])
                                        if any(term in row_text for term in ['professor', 'lecturer', 'instructor']):
                                            has_professor_data = True
                                            break
                                    
                                    # If no professor titles found, this is probably consultancy data
                                    if not has_professor_data:
                                        continue
                                
                                if keyword_matches > max_keyword_matches:
                                    max_keyword_matches = keyword_matches
                                    matched_category = category
                        
                        if matched_category:
                            # Store this table for this category
                            # Keep only the BEST (highest score) table of each type per page
                            if matched_category not in page_tables or page_tables[matched_category][1] < max_keyword_matches:
                                page_tables[matched_category] = (df, max_keyword_matches, table_idx)
                        else:
                            print(f"  Table {table_idx+1}: No match (shape: {df.shape})")
                    
                    # Now add the best table of each category from this page
                    for category, (df, score, table_idx) in page_tables.items():
                        title = table_keywords[category]["title"]
                        
                        # Special handling for large tables
                        skip_large = False
                        if title in ["Student Intake", "Library Details"]:
                            if df.shape[0] > 30:
                                skip_large = True
                        
                        if not skip_large:
                            df["Source_Page"] = page_num
                            tables_with_titles.append((df, title))
                            print(f"  Table {table_idx+1}: Matched as '{title}' ({score} keywords)")
                
                except Exception as e:
                    print(f"Error on page {page_num}: {e}")
        
        print(f"\nTotal tables extracted: {len(tables_with_titles)}\n")

        # Save to Excel
        if tables_with_titles:
            output_excel = BytesIO()
            with pd.ExcelWriter(output_excel, engine="openpyxl") as writer:
                title_counts = {}
                for table, title in tables_with_titles:
                    title_counts[title] = title_counts.get(title, 0) + 1
                    sheet_name = f"{title} ({title_counts[title]})" if title_counts[title] > 1 else title
                    sheet_name = sheet_name[:31]  # Excel limit
                    
                    table.to_excel(writer, sheet_name=sheet_name, index=False, header=False)
                    
                    # Format columns
                    workbook = writer.book
                    worksheet = writer.sheets[sheet_name]
                    
                    for col in worksheet.columns:
                        max_length = 0
                        column = col[0].column_letter
                        for cell in col:
                            try:
                                if len(str(cell.value)) > max_length:
                                    max_length = len(cell.value)
                            except:
                                pass
                        adjusted_width = min(max_length + 2, 50)
                        worksheet.column_dimensions[column].width = adjusted_width
                    
                    print(f"Saved sheet: {sheet_name}")

            output_excel.seek(0)
            excel_bytes = output_excel.read()

            # Save to database
            processed_data = excel_data(
                college_name=info.college_name,
                college_intake=str(info.college_intake)
            )
            processed_data.file_data.put(BytesIO(excel_bytes), 
                                        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            processed_data.save()

            print("\nExcel data saved to database")
            print("Creating compliance report...")
            
            report_response = await create_compliance_report(info)
            document_check = check_missing_documents(info.college_name, info.college_intake)
            return {
                "message": "Compliance report processed successfully",
                "report_response": report_response,
                "document_completeness": document_check
            }
        else:
            raise HTTPException(status_code=404, detail="No relevant tables found.")

    except Exception as e:
        error_msg = f"{str(e)}\n{traceback.format_exc()}"
        print(f"Error in process_mandatory_disclosure: {error_msg}")
        raise HTTPException(status_code=500, detail=str(e))


def build_clause_compliance(faculty_data, infrastructure_data, student_faculty_ratio,
                           faculty_qualification_experience, document_completeness,
                           approval_certificate_validity, student_intake):
    """
    Build clause-wise compliance analysis for all compliance areas.
    """
    try:
        clauses = []

        # Faculty Count Clauses
        clauses.append({
            "clause_id": "FAC-01",
            "clause_name": "Professor Count",
            "actual_value": faculty_data['professors'],
            "required_value": faculty_data['required_professors'],
            "status": "Compliant" if faculty_data['professors'] >= faculty_data['required_professors'] else "Non-Compliant",
            "remarks": f"Required: {faculty_data['required_professors']}, Available: {faculty_data['professors']}"
        })

        clauses.append({
            "clause_id": "FAC-02",
            "clause_name": "Associate Professor Count",
            "actual_value": faculty_data['associate_professors'],
            "required_value": faculty_data['required_associate_professors'],
            "status": "Compliant" if faculty_data['associate_professors'] >= faculty_data['required_associate_professors'] else "Non-Compliant",
            "remarks": f"Required: {faculty_data['required_associate_professors']}, Available: {faculty_data['associate_professors']}"
        })

        clauses.append({
            "clause_id": "FAC-03",
            "clause_name": "Assistant Professor Count",
            "actual_value": faculty_data['assistant_professors'],
            "required_value": faculty_data['required_assistant_professors'],
            "status": "Compliant" if faculty_data['assistant_professors'] >= faculty_data['required_assistant_professors'] else "Non-Compliant",
            "remarks": f"Required: {faculty_data['required_assistant_professors']}, Available: {faculty_data['assistant_professors']}"
        })

        # Infrastructure Clauses
        clauses.append({
            "clause_id": "INF-01",
            "clause_name": "Classroom Count",
            "actual_value": infrastructure_data['classrooms'],
            "required_value": infrastructure_data['required_classrooms'],
            "status": "Compliant" if infrastructure_data['classrooms'] >= infrastructure_data['required_classrooms'] else "Non-Compliant",
            "remarks": f"Required: {infrastructure_data['required_classrooms']}, Available: {infrastructure_data['classrooms']}"
        })

        clauses.append({
            "clause_id": "INF-02",
            "clause_name": "Laboratory Count",
            "actual_value": infrastructure_data['labs'],
            "required_value": infrastructure_data['required_labs'],
            "status": "Compliant" if infrastructure_data['labs'] >= infrastructure_data['required_labs'] else "Non-Compliant",
            "remarks": f"Required: {infrastructure_data['required_labs']}, Available: {infrastructure_data['labs']}"
        })

        clauses.append({
            "clause_id": "INF-03",
            "clause_name": "Workshop Count",
            "actual_value": infrastructure_data['workshops'],
            "required_value": infrastructure_data['required_workshops'],
            "status": "Compliant" if infrastructure_data['workshops'] >= infrastructure_data['required_workshops'] else "Non-Compliant",
            "remarks": f"Required: {infrastructure_data['required_workshops']}, Available: {infrastructure_data['workshops']}"
        })

        clauses.append({
            "clause_id": "INF-04",
            "clause_name": "Smart Classroom Count",
            "actual_value": infrastructure_data['smart_classrooms'],
            "required_value": infrastructure_data['required_smart_classrooms'],
            "status": "Compliant" if infrastructure_data['smart_classrooms'] >= infrastructure_data['required_smart_classrooms'] else "Non-Compliant",
            "remarks": f"Required: {infrastructure_data['required_smart_classrooms']}, Available: {infrastructure_data['smart_classrooms']}"
        })

        # Student-Faculty Ratio Clause
        ratio_status = student_faculty_ratio.get('status', 'Not Available')
        clauses.append({
            "clause_id": "SFR-01",
            "clause_name": "Student-Faculty Ratio",
            "actual_value": student_faculty_ratio.get('ratio', 'Not Available'),
            "required_value": f"≤{student_faculty_ratio.get('required_max_ratio', 20)}",
            "status": ratio_status,
            "remarks": f"Current ratio: {student_faculty_ratio.get('ratio', 'N/A')}, Max allowed: {student_faculty_ratio.get('required_max_ratio', 20)}"
        })

        # Faculty Qualification Validation
        qual_valid = 0
        qual_invalid = 0
        qual_total = 0
        for faculty in faculty_qualification_experience:
            if faculty.get('qualification_status') == 'Available':
                qual_valid += faculty.get('qualification_valid', 0)
                qual_invalid += faculty.get('qualification_invalid', 0)
                qual_total += faculty.get('total', 0)

        qual_status = "Not Available"
        if qual_total > 0:
            if qual_invalid == 0:
                qual_status = "Compliant"
            elif qual_invalid / qual_total <= 0.2:  # Less than 20% invalid
                qual_status = "Warning"
            else:
                qual_status = "Non-Compliant"

        clauses.append({
            "clause_id": "FQV-01",
            "clause_name": "Faculty Qualification Validation",
            "actual_value": f"{qual_valid} valid, {qual_invalid} invalid",
            "required_value": "100% valid qualifications",
            "status": qual_status,
            "remarks": f"Total faculty: {qual_total}, Valid: {qual_valid}, Invalid: {qual_invalid}"
        })

        # Faculty Experience Validation
        exp_valid = 0
        exp_invalid = 0
        exp_total = 0
        for faculty in faculty_qualification_experience:
            if faculty.get('experience_status') == 'Available':
                exp_valid += faculty.get('experience_valid', 0)
                exp_invalid += faculty.get('experience_invalid', 0)
                exp_total += faculty.get('total', 0)

        exp_status = "Not Available"
        if exp_total > 0:
            if exp_invalid == 0:
                exp_status = "Compliant"
            elif exp_invalid / exp_total <= 0.2:  # Less than 20% invalid
                exp_status = "Warning"
            else:
                exp_status = "Non-Compliant"

        clauses.append({
            "clause_id": "FEV-01",
            "clause_name": "Faculty Experience Validation",
            "actual_value": f"{exp_valid} valid, {exp_invalid} invalid",
            "required_value": "100% valid experience",
            "status": exp_status,
            "remarks": f"Total faculty: {exp_total}, Valid: {exp_valid}, Invalid: {exp_invalid}"
        })

        # Mandatory Document Completeness
        doc_present = document_completeness.get('present_count', 0)
        doc_required = document_completeness.get('required_count', 0)
        doc_percentage = document_completeness.get('completeness_percentage', 0)

        doc_status = "Not Available"
        if doc_required > 0:
            if doc_percentage >= 100:
                doc_status = "Compliant"
            elif doc_percentage >= 80:
                doc_status = "Warning"
            else:
                doc_status = "Non-Compliant"

        clauses.append({
            "clause_id": "DOC-01",
            "clause_name": "Mandatory Document Completeness",
            "actual_value": f"{doc_present}/{doc_required} ({doc_percentage}%)",
            "required_value": "100% completeness",
            "status": doc_status,
            "remarks": f"Present: {doc_present}, Required: {doc_required}, Completeness: {doc_percentage}%"
        })

        # Approval / Certificate Validity
        cert_valid = 0
        cert_expiring = 0
        cert_expired = 0
        cert_total = len(approval_certificate_validity)

        for cert in approval_certificate_validity:
            status = cert.get('status', 'Not Available')
            if status == 'Valid':
                cert_valid += 1
            elif status == 'Expiring Soon':
                cert_expiring += 1
            elif status == 'Expired':
                cert_expired += 1

        cert_status = "Not Available"
        if cert_total > 0:
            if cert_expired == 0 and cert_expiring == 0:
                cert_status = "Compliant"
            elif cert_expired == 0:
                cert_status = "Warning"
            else:
                cert_status = "Non-Compliant"

        clauses.append({
            "clause_id": "ACV-01",
            "clause_name": "Approval / Certificate Validity",
            "actual_value": f"{cert_valid} valid, {cert_expiring} expiring, {cert_expired} expired",
            "required_value": "All certificates valid",
            "status": cert_status,
            "remarks": f"Total certificates: {cert_total}, Valid: {cert_valid}, Expiring: {cert_expiring}, Expired: {cert_expired}"
        })

        return clauses

    except Exception as e:
        print(f"Error in build_clause_compliance: {e}")
        traceback.print_exc()
        return []


def build_document_image_crosscheck(infrastructure_data, image_evidence):
    """Compare document inferred counts with image evidence and produce cross-validation output."""
    try:
        doc_classrooms = infrastructure_data.get('classrooms', 0)
        doc_labs = infrastructure_data.get('labs', 0)
        doc_smart = infrastructure_data.get('smart_classrooms', 0)

        img_classrooms = image_evidence.get('classroom_image_count', 0)
        img_classrooms_valid = image_evidence.get('classroom_valid_count', 0)

        img_labs = image_evidence.get('lab_image_count', 0)
        img_labs_valid = image_evidence.get('lab_valid_count', 0)

        img_smart = image_evidence.get('smart_classroom_evidence', 0)

        def evidence_status(declared, image_count, valid_count):
            if image_count == 0:
                return 'missing'
            if valid_count >= declared and image_count >= declared:
                return 'sufficient'
            if valid_count > 0 or image_count > 0:
                return 'partial'
            return 'missing'

        classroom_evidence_status = evidence_status(doc_classrooms, img_classrooms, img_classrooms_valid)
        lab_evidence_status = evidence_status(doc_labs, img_labs, img_labs_valid)
        smart_evidence_status = 'sufficient' if img_smart >= doc_smart and doc_smart > 0 else ('partial' if img_smart > 0 else 'missing')

        flags = []
        if img_classrooms == 0 and doc_classrooms > 0:
            flags.append('Classroom image evidence missing')
        if img_labs == 0 and doc_labs > 0:
            flags.append('Lab image evidence missing')
        if doc_smart > 0 and img_smart == 0:
            flags.append('Smart classroom claim has no visual evidence')

        if doc_classrooms > img_classrooms_valid:
            flags.append('Document classroom count exceeds valid room evidence')
        if doc_labs > img_labs_valid:
            flags.append('Document lab count exceeds valid room evidence')
        if doc_smart > img_smart:
            flags.append('Document smart classroom claim exceeds image evidence')

        overall = 'Compliant'
        if any(x.startswith('Document') or 'missing' in x.lower() or 'exceeds' in x.lower() for x in flags):
            overall = 'Non-Compliant'
        elif any(status == 'partial' for status in [classroom_evidence_status, lab_evidence_status, smart_evidence_status]):
            overall = 'Partial'

        return {
            'document_classrooms': doc_classrooms,
            'image_classrooms': img_classrooms,
            'classroom_valid_rooms': img_classrooms_valid,
            'classroom_evidence_status': classroom_evidence_status,
            'document_labs': doc_labs,
            'image_labs': img_labs,
            'lab_valid_rooms': img_labs_valid,
            'lab_evidence_status': lab_evidence_status,
            'document_smart_classrooms': doc_smart,
            'image_smart_evidence': img_smart,
            'smart_evidence_status': smart_evidence_status,
            'flags': flags,
            'overall_cross_validation_status': overall,
        }

    except Exception as e:
        print(f"Error in build_document_image_crosscheck: {e}")
        traceback.print_exc()
        return {
            'flags': ['Error in crosscheck logic'],
            'overall_cross_validation_status': 'Error'
        }


def calculate_final_compliance_score(clauses):
    """
    Calculate final compliance score based on clause statuses.
    """
    try:
        score = 100

        # Define important clauses that get higher penalties
        important_clauses = [
            'FAC-01', 'FAC-02', 'FAC-03',  # Faculty counts
            'INF-01', 'INF-02',  # Classrooms and Labs
            'DOC-01', 'ACV-01'  # Documents and Certificates
        ]

        for clause in clauses:
            status = clause.get('status', 'Not Available')
            clause_id = clause.get('clause_id', '')

            if status == 'Non-Compliant':
                if clause_id in important_clauses:
                    score -= 10  # Higher penalty for important non-compliant clauses
                else:
                    score -= 5   # Standard penalty
            elif status == 'Warning':
                score -= 5   # Warning penalty

        # Additional penalty for missing important documents
        for clause in clauses:
            if clause.get('clause_id') == 'DOC-01':
                if clause.get('status') == 'Non-Compliant':
                    # Already penalized above, but add extra for document completeness
                    doc_percentage = 0
                    actual_value = clause.get('actual_value', '')
                    if '%' in actual_value:
                        try:
                            doc_percentage = int(actual_value.split('%')[0].split()[-1])
                        except:
                            pass
                    if doc_percentage < 80:
                        score -= 3  # Extra penalty for low document completeness

        # Clamp score between 0 and 100
        score = max(0, min(100, score))

        # Determine final status
        if score >= 85:
            final_status = "Compliant"
        elif score >= 70:
            final_status = "Partially Compliant"
        elif score >= 50:
            final_status = "Moderate Risk"
        else:
            final_status = "Non-Compliant"

        return {
            "final_score": score,
            "final_status": final_status
        }

    except Exception as e:
        print(f"Error in calculate_final_compliance_score: {e}")
        traceback.print_exc()
        return {
            "final_score": 0,
            "final_status": "Error"
        }


@app.post("/create-compliance-report/")
async def create_compliance_report(info: CollegeLoginInfo):
    try:
        # Load excel data
        # Fetch the most recently saved excel for this college+intake (avoid returning older files)
        excel_file_obj = excel_data.objects(
            college_name=info.college_name,
            college_intake=str(info.college_intake)
        ).order_by('-id').first()

        if not excel_file_obj:
            raise HTTPException(status_code=404, detail="Excel not found")

        print(f"\nLoading excel file for {info.college_name}...")
        file_content = safe_read_file_field(excel_file_obj.file_data)
        
        if not file_content:
            raise HTTPException(status_code=500, detail="Excel file data is empty or inaccessible")
        
        if isinstance(file_content, bytes):
            excel_file = BytesIO(file_content)
        elif isinstance(file_content, str):
            excel_file = BytesIO(file_content.encode('utf-8'))
        else:
            try:
                excel_file = BytesIO(bytes(file_content))
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Cannot convert file data: {type(file_content).__name__}")
        
        print(f"Excel file loaded: {len(file_content)} bytes")

    except Exception as e:
        error_msg = f"{str(e)}\n{traceback.format_exc()}"
        print(f"Error loading excel file: {error_msg}")
        raise HTTPException(status_code=500, detail=str(e))
    
    def analyze_faculty_data(excel_file):
        """
        Improved faculty analysis that correctly identifies faculty tables
        and counts designations accurately.
        """
        try:
            excel_file.seek(0)
            excel_data_obj = pd.ExcelFile(excel_file)
            
            total_professors = 0
            total_associate_professors = 0
            total_assistant_professors = 0

            print(f"\n{'='*80}")
            print(f"FACULTY ANALYSIS")
            print(f"{'='*80}\n")
            
            for sheet_name in excel_data_obj.sheet_names:
                try:
                    excel_file.seek(0)
                    df = pd.read_excel(excel_file, sheet_name=sheet_name, header=None)
                    
                    if df.shape[0] < 2 or df.shape[1] < 2:
                        continue
                    
                    # Skip non-faculty sheets based on name patterns
                    sheet_lower = sheet_name.lower()
                    
                    # Skip if it's clearly not faculty data
                    if any(x in sheet_lower for x in ['intake', 'classroom', 'lab', 'library', 'course', 'workshop', 'smart', 'information', 'details']):
                        # Exception: keep "Faculty Information" sheets
                        if 'faculty' not in sheet_lower:
                            print(f"Sheet '{sheet_name}': SKIPPED (not a faculty sheet)")
                            continue
                    
                    # Check if this is a faculty sheet
                    first_row_text = ' '.join([str(x).lower().strip() for x in df.iloc[0]])
                    
                    # Check if it contains "Faculty Information" in name OR has designation column
                    is_named_faculty = 'faculty' in sheet_lower
                    has_designation = 'designation' in first_row_text or 'position' in first_row_text
                    
                    # Also check for faculty keywords in headers
                    faculty_keywords = ['designation', 'professor', 'name', 'qualification', 'department', 'faculty']
                    keyword_count = sum(1 for kw in faculty_keywords if kw in first_row_text)
                    
                    if not is_named_faculty and not has_designation and keyword_count < 2:
                        print(f"Sheet '{sheet_name}': SKIPPED (not identified as faculty data)")
                        continue
                    
                    print(f"Sheet '{sheet_name}': PROCESSING")
                    print(f"  Shape: {df.shape}")
                    print(f"  First row: {df.iloc[0].tolist()[:6]}")
                    
                    # Find designation column
                    designation_col = None
                    
                    # Check first row (header) - PRIORITY
                    for col_idx in range(df.shape[1]):
                        header = str(df.iloc[0, col_idx]).lower().strip()
                        if 'designation' in header or 'position' in header:
                            designation_col = col_idx
                            print(f"  Designation column found at index {col_idx} (header)")
                            break
                    
                    # If not found in header, scan data for designation patterns
                    if designation_col is None:
                        for col_idx in range(min(6, df.shape[1])):
                            prof_count = 0
                            for row_idx in range(1, min(15, df.shape[0])):
                                cell = str(df.iloc[row_idx, col_idx]).lower().strip()
                                # Check for professor titles
                                if any(term in cell for term in ['professor', 'associate', 'assistant', 'lecturer', 'instructor']):
                                    prof_count += 1
                            
                            if prof_count >= 2:
                                designation_col = col_idx
                                print(f"  Designation column found at index {col_idx} (data scan, {prof_count} matches)")
                                break
                    
                    if designation_col is None:
                        print(f"  No designation column found, skipping")
                        continue
                    
                    # Count faculty by designation
                    profs = assoc = asst = 0
                    
                    # Start from row 1 (row 0 is header)
                    for row_idx in range(1, df.shape[0]):
                        cell = str(df.iloc[row_idx, designation_col]).strip()
                        
                        # Skip empty cells and non-string values
                        if not cell or cell.lower() in ['nan', '', 'none', 'designation', 'position']:
                            continue
                        
                        cell_lower = cell.lower()
                        
                        # Count based on designation
                        # CRITICAL: Check "associate professor" BEFORE "professor" to avoid miscounting
                        if 'associate professor' in cell_lower or 'associate prof' in cell_lower or 'assoc prof' in cell_lower or 'assoc. prof' in cell_lower:
                            assoc += 1
                        elif 'assistant professor' in cell_lower or 'assistant prof' in cell_lower or 'asst professor' in cell_lower or 'asst prof' in cell_lower or 'asst. prof' in cell_lower:
                            asst += 1
                        elif 'professor' in cell_lower or cell_lower == 'prof':
                            # Make sure it's not associate or assistant
                            if 'associate' not in cell_lower and 'assistant' not in cell_lower and 'asst' not in cell_lower:
                                profs += 1
                    
                    print(f"  Faculty found: Professors={profs}, Associate={assoc}, Assistant={asst}")
                    
                    total_professors += profs
                    total_associate_professors += assoc
                    total_assistant_professors += asst
                    
                except Exception as e:
                    print(f"  Error processing sheet '{sheet_name}': {e}")
                    import traceback
                    traceback.print_exc()
                    continue

            print(f"\n{'='*80}")
            print(f"FACULTY TOTALS")
            print(f"{'='*80}")
            print(f"Professors: {total_professors}")
            print(f"Associate Professors: {total_associate_professors}")
            print(f"Assistant Professors: {total_assistant_professors}")
            print(f"{'='*80}\n")

            return total_professors, total_associate_professors, total_assistant_professors
            
        except Exception as e:
            print(f"Error in analyze_faculty_data: {e}")
            traceback.print_exc()
            return 0, 0, 0

    def validate_faculty_qualification_experience(excel_file):
        """
        Validate faculty qualification and experience based on simple rules.
        """
        excel_file.seek(0)
        try:
            excel_data_obj = pd.ExcelFile(excel_file)
            summary = {
                'Professor': {'total': 0, 'qualification_valid': 0, 'qualification_invalid': 0, 'experience_valid': 0, 'experience_invalid': 0},
                'Associate Professor': {'total': 0, 'qualification_valid': 0, 'qualification_invalid': 0, 'experience_valid': 0, 'experience_invalid': 0},
                'Assistant Professor': {'total': 0, 'qualification_valid': 0, 'qualification_invalid': 0, 'experience_valid': 0, 'experience_invalid': 0}
            }

            # Keep track if we found qualification/experience columns anywhere
            found_qualification = False
            found_experience = False

            def parse_experience(value):
                if value is None:
                    return None
                text = str(value).lower().strip()
                if not text or text in ['nan', 'none', 'na', '-']:
                    return None
                # accept patterns like '10', '10 years', '8+', '12 yrs', '5 yr'
                m = re.search(r"(\d+)(\s*\+)?", text)
                if not m:
                    return None
                try:
                    return int(m.group(1))
                except Exception:
                    return None

            for sheet_name in excel_data_obj.sheet_names:
                try:
                    excel_file.seek(0)
                    df = pd.read_excel(excel_file, sheet_name=sheet_name, header=None)
                    if df.shape[0] < 2 or df.shape[1] < 2:
                        continue

                    first_row_text = ' '.join([str(x).lower().strip() for x in df.iloc[0]])
                    # Skip clearly unrelated sheets
                    if any(x in sheet_name.lower() for x in ['intake', 'classroom', 'lab', 'library', 'workshop', 'smart', 'information', 'details']) and 'faculty' not in sheet_name.lower():
                        continue

                    # Determine column indices
                    designation_col = None
                    qualification_col = None
                    experience_col = None

                    for col_idx in range(df.shape[1]):
                        header = str(df.iloc[0, col_idx]).lower().strip()
                        if designation_col is None and ('designation' in header or 'position' in header):
                            designation_col = col_idx
                        if qualification_col is None and ('qualification' in header or 'degree' in header):
                            qualification_col = col_idx
                        if experience_col is None and ('experience' in header or 'yrs' in header or 'year' in header or 'exp' in header):
                            experience_col = col_idx

                    # Fallback: scan columns if designation not found explicitly
                    if designation_col is None:
                        for col_idx in range(min(6, df.shape[1])):
                            prof_count = 0
                            for row_idx in range(1, min(15, df.shape[0])):
                                cell = str(df.iloc[row_idx, col_idx]).lower().strip()
                                if any(term in cell for term in ['professor', 'associate', 'assistant', 'lecturer', 'instructor']):
                                    prof_count += 1
                            if prof_count >= 2:
                                designation_col = col_idx
                                break

                    if designation_col is None:
                        continue

                    if qualification_col is not None:
                        found_qualification = True
                    if experience_col is not None:
                        found_experience = True

                    for row_idx in range(1, df.shape[0]):
                        desig_cell = str(df.iloc[row_idx, designation_col]).strip().lower()
                        if not desig_cell or desig_cell in ['nan', 'none', 'designation', 'position']:
                            continue

                        # Determine designation category
                        if 'associate professor' in desig_cell or 'associate prof' in desig_cell or 'assoc' in desig_cell:
                            key = 'Associate Professor'
                        elif 'assistant professor' in desig_cell or 'assistant prof' in desig_cell or 'asst' in desig_cell:
                            key = 'Assistant Professor'
                        elif 'professor' in desig_cell or desig_cell == 'prof':
                            # avoid double counting associate/assistant
                            if 'associate' in desig_cell or 'assistant' in desig_cell or 'asst' in desig_cell:
                                continue
                            key = 'Professor'
                        else:
                            continue

                        summary[key]['total'] += 1

                        # Qualification validation
                        if qualification_col is None:
                            pass
                        else:
                            qual_value = str(df.iloc[row_idx, qualification_col]).lower().strip()
                            if qual_value and qual_value not in ['nan', 'none', '']:
                                is_phd = 'phd' in qual_value or 'ph.d' in qual_value
                                is_mtech = 'm.tech' in qual_value or 'mtech' in qual_value or 'm.tech.' in qual_value
                                if key == 'Professor':
                                    if is_phd:
                                        summary[key]['qualification_valid'] += 1
                                    else:
                                        summary[key]['qualification_invalid'] += 1
                                elif key == 'Associate Professor':
                                    if is_phd or is_mtech:
                                        summary[key]['qualification_valid'] += 1
                                    else:
                                        summary[key]['qualification_invalid'] += 1
                                elif key == 'Assistant Professor':
                                    if is_phd or is_mtech:
                                        summary[key]['qualification_valid'] += 1
                                    else:
                                        summary[key]['qualification_invalid'] += 1
                            else:
                                summary[key]['qualification_invalid'] += 1

                        # Experience validation
                        if experience_col is None:
                            pass
                        else:
                            exp_value = df.iloc[row_idx, experience_col]
                            years = parse_experience(exp_value)

                            if years is None:
                                summary[key]['experience_invalid'] += 1
                            else:
                                if key == 'Professor':
                                    if years >= 10:
                                        summary[key]['experience_valid'] += 1
                                    else:
                                        summary[key]['experience_invalid'] += 1
                                elif key == 'Associate Professor':
                                    if years >= 8:
                                        summary[key]['experience_valid'] += 1
                                    else:
                                        summary[key]['experience_invalid'] += 1
                                elif key == 'Assistant Professor':
                                    if years >= 0:
                                        summary[key]['experience_valid'] += 1
                                    else:
                                        summary[key]['experience_invalid'] += 1

                except Exception as e:
                    print(f"  Error validating sheet '{sheet_name}': {e}")
                    import traceback
                    traceback.print_exc()
                    continue

            output = []
            for designation in ['Professor', 'Associate Professor', 'Assistant Professor']:
                rec = {
                    'designation': designation,
                    'total': summary[designation]['total'],
                    'qualification_valid': summary[designation]['qualification_valid'] if found_qualification else 'Not Available',
                    'qualification_invalid': summary[designation]['qualification_invalid'] if found_qualification else 'Not Available',
                    'experience_valid': summary[designation]['experience_valid'] if found_experience else 'Not Available',
                    'experience_invalid': summary[designation]['experience_invalid'] if found_experience else 'Not Available',
                    'qualification_status': 'Available' if found_qualification else 'Not Available',
                    'experience_status': 'Available' if found_experience else 'Not Available'
                }
                output.append(rec)

            return output

        except Exception as e:
            print(f"Error in validate_faculty_qualification_experience: {e}")
            traceback.print_exc()
            return [
                {'designation': 'Professor', 'total': 0, 'qualification_valid': 'Not Available', 'qualification_invalid': 'Not Available', 'experience_valid': 'Not Available', 'experience_invalid': 'Not Available', 'qualification_status': 'Not Available', 'experience_status': 'Not Available'},
                {'designation': 'Associate Professor', 'total': 0, 'qualification_valid': 'Not Available', 'qualification_invalid': 'Not Available', 'experience_valid': 'Not Available', 'experience_invalid': 'Not Available', 'qualification_status': 'Not Available', 'experience_status': 'Not Available'},
                {'designation': 'Assistant Professor', 'total': 0, 'qualification_valid': 'Not Available', 'qualification_invalid': 'Not Available', 'experience_valid': 'Not Available', 'experience_invalid': 'Not Available', 'qualification_status': 'Not Available', 'experience_status': 'Not Available'}
            ]

    def analyze_infrastructure_data(excel_file):
        """
        Improved infrastructure analysis with better categorization.
        """
        try:
            excel_file.seek(0)
            excel_data_obj = pd.ExcelFile(excel_file)

            total_labs = 0
            total_classrooms = 0
            total_dept_library = 0
            workshops = 0
            smart_classroom = 0
            unique_labs = set()
            
            print(f"\n{'='*80}")
            print(f"INFRASTRUCTURE ANALYSIS")
            print(f"{'='*80}\n")
            
            for sheet_name in excel_data_obj.sheet_names:
                try:
                    excel_file.seek(0)
                    df = pd.read_excel(excel_file, sheet_name=sheet_name, header=None)
                    
                    if df.shape[0] < 2 or df.shape[1] < 2:
                        continue
                    
                    sheet_lower = sheet_name.lower()
                    
                    # Skip non-infrastructure sheets
                    if any(x in sheet_lower for x in ['faculty', 'intake', 'course', 'student', 'college']):
                        continue
                    
                    print(f"\nSheet '{sheet_name}': PROCESSING")
                    print(f"  Category: {sheet_name}")
                    
                    # Check if sheet contains "Room Type" column (universal indicator of infrastructure data)
                    first_row_list = [str(x).lower().strip() for x in df.iloc[0]]
                    first_row = ' '.join(first_row_list)
                    has_room_type_col = any('room type' in h or h == 'type' or 'room' in h for h in first_row_list)
                    
                    # Find the room type column by header heuristics
                    room_type_col = None
                    for col_idx, header in enumerate(first_row_list):
                        if 'room type' in header or header == 'type' or 'room' in header:
                            room_type_col = col_idx
                            break
                    # Fallback: if header didn't explicitly contain room/type but sheet name indicates classroom/smart, assume column 2 is room type
                    if room_type_col is None and ('classroom' in sheet_lower or 'smart' in sheet_lower):
                        if df.shape[1] >= 3:
                            room_type_col = 2
                    
                    # If we found a room type column, count by room types
                    if room_type_col is not None:
                        print(f"  Counted from room type column (index {room_type_col})")
                        
                        classroom_count = 0
                        lab_count = 0
                        workshop_count = 0
                        smart_count = 0
                        library_count = 0
                        
                        for row_idx in range(1, df.shape[0]):
                            try:
                                # Build a normalized text for the whole row to catch 'smart' or similar markers in other columns
                                row_text = ' '.join([str(df.iloc[row_idx, c]).lower().strip() for c in range(df.shape[1]) if df.shape[1] > c])

                                if not row_text or row_text == 'nan' or row_text.strip() == '':
                                    continue

                                # Count based on row text. Check smart indicators across the entire row first.
                                if 'smart classroom' in row_text or ('smart' in row_text and 'class' in row_text) or ('smart' in row_text and 'projector' in row_text) or 'interactive' in row_text:
                                    smart_count += 1
                                elif 'laboratory' in row_text or ' lab ' in row_text or row_text.startswith('lab'):
                                    lab_count += 1
                                    # Prefer lab name if available in the row; fall back to row_text
                                    # Try to pick a concise lab identifier from the row
                                    lab_id = None
                                    try:
                                        # common lab name column might be column 1 or 0; prefer non-empty cells
                                        for c in range(df.shape[1]):
                                            val = str(df.iloc[row_idx, c]).strip()
                                            if val and val.lower() not in ['nan', 'none', '']:
                                                lab_id = val.lower()
                                                break
                                    except:
                                        lab_id = row_text
                                    unique_labs.add(lab_id or row_text)
                                elif 'workshop' in row_text:
                                    workshop_count += 1
                                elif 'library' in row_text and ('dept' in row_text or 'department' in row_text or 'library' in row_text):
                                    library_count += 1
                                elif 'classroom' in row_text:
                                    # Exclude smart classrooms already counted
                                    if 'smart' not in row_text:
                                        classroom_count += 1
                            except:
                                continue
                        
                        total_classrooms += classroom_count
                        total_labs += lab_count
                        workshops += workshop_count
                        smart_classroom += smart_count
                        total_dept_library += library_count
                        
                        print(f"  Classrooms: {classroom_count}, Labs: {lab_count}, Workshops: {workshop_count}, Smart: {smart_count}, Libraries: {library_count}")
                    else:
                        # No room type column - this sheet structure is not recognized
                        # Don't count rows directly - instead, only process if it matches specific patterns
                        
                        # Only count sheets with proper lab/workshop/classroom structure
                        if 'lab' in sheet_lower and ('information' in sheet_lower or 'details' in sheet_lower):
                            # Lab Information sheets should have a name or ID column to count
                            # Count only non-empty first column as lab names
                            lab_name_col = 0
                            lab_count = 0
                            for row_idx in range(1, df.shape[0]):
                                try:
                                    cell = str(df.iloc[row_idx, lab_name_col]).strip()
                                    if cell and cell.lower() != 'nan':
                                        lab_count += 1
                                except:
                                    continue
                            total_labs += lab_count
                            print(f"  Category: LABORATORY - Counted {lab_count} labs from name column")
                        
                        elif 'workshop' in sheet_lower:
                            # Similar logic for workshops
                            workshop_count = 0
                            for row_idx in range(1, df.shape[0]):
                                try:
                                    cell = str(df.iloc[row_idx, 0]).strip()
                                    if cell and cell.lower() != 'nan':
                                        workshop_count += 1
                                except:
                                    continue
                            workshops += workshop_count
                            print(f"  Category: WORKSHOP - Counted {workshop_count} workshops")
                        
                        elif 'library' in sheet_lower:
                            total_dept_library += 1
                            print(f"  Category: LIBRARY - Found 1 library")
                        
                        else:
                            # Unknown sheet structure - skip it
                            print(f"  Skipped: No recognized structure (no room type column)")
                
                except Exception as e:
                    print(f"  Error processing sheet '{sheet_name}': {e}")
                    import traceback
                    traceback.print_exc()
                    continue
            
            print(f"\n{'='*80}")
            print(f"INFRASTRUCTURE TOTALS")
            print(f"{'='*80}")
            print(f"Classrooms: {total_classrooms}")
            print(f"Smart Classrooms: {smart_classroom}")
            print(f"Laboratories: {len(unique_labs) if unique_labs else total_labs}")
            print(f"Workshops: {workshops}")
            print(f"Department Libraries: {total_dept_library}")
            print(f"{'='*80}\n")
            
            final_lab_count = len(unique_labs) if unique_labs else total_labs
            return final_lab_count, total_classrooms, total_dept_library, workshops, smart_classroom
            
        except Exception as e:
            print(f"Error in analyze_infrastructure_data: {e}")
            traceback.print_exc()
            return 0, 0, 0, 0, 0
    
    def validate_classroom_details(excel_file):
        """
        Validate classroom sizes against AICTE norms.
        Detects validation data by table headers (room type + area columns).
        """
        try:
            excel_file.seek(0)
            excel_data_obj = pd.ExcelFile(excel_file)
            
            validation_results = []
            
            print(f"\n{'='*80}")
            print(f"CLASSROOM SIZE VALIDATION")
            print(f"{'='*80}\n")
            
            for sheet_name in excel_data_obj.sheet_names:
                try:
                    excel_file.seek(0)
                    df = pd.read_excel(excel_file, sheet_name=sheet_name, header=None)
                    
                    if df.shape[0] < 2 or df.shape[1] < 3:
                        continue
                    
                    # Detect validation sheets by their headers (not by sheet name)
                    # Look for tables with "room type" / "area" OR "laboratory" / "area" OR "workshop" / "area"
                    first_row = [str(x).lower().strip() for x in df.iloc[0]]
                    headers_combined = ' '.join(first_row)
                    
                    # Check if this table has relevant room/infrastructure columns
                    has_room_type = any('room' in h or 'laboratory' in h or 'workshop' in h for h in first_row)
                    has_area = any('area' in h or 'sq' in h for h in first_row)
                    
                    if not (has_room_type and has_area):
                        continue  # Not an infrastructure/validation table
                    
                    # Find the columns
                    room_type_col = None
                    area_col = None
                    
                    for idx, header in enumerate(first_row):
                        if 'type' in header or 'room' in header or 'laboratory' in header or 'workshop' in header:
                            room_type_col = idx
                        if 'area' in header or 'sq' in header:
                            area_col = idx
                    
                    # Fallback: assume column 2 is type, column 3 is area
                    if room_type_col is None and df.shape[1] >= 3:
                        room_type_col = 2
                    if area_col is None and df.shape[1] >= 4:
                        area_col = 3
                    elif area_col is None and df.shape[1] == 3:
                        area_col = 2
                    
                    if room_type_col is None or area_col is None:
                        continue
                    
                    print(f"Validating sheet: {sheet_name} (room_type_col={room_type_col}, area_col={area_col})")
                    
                    # Validate each row
                    for row_idx in range(1, df.shape[0]):
                        room_type = str(df.iloc[row_idx, room_type_col]).strip().lower()
                        area_val = df.iloc[row_idx, area_col]
                        
                        if not room_type or room_type == 'nan':
                            continue
                        
                        status = "Valid"
                        
                        try:
                            area = float(area_val)

                            # AICTE norms for room sizes
                            # Treat 'smart' presence as classroom-type for validation
                            if 'classroom' in room_type or 'laboratory' in room_type or 'smart' in room_type:
                                if area < 66:
                                    status = f"Invalid. Room is {66 - area:.1f} sq.m smaller than required (66 sq.m)"
                            elif "workshop" in room_type:
                                if area < 200:
                                    status = f"Invalid. Room is {200 - area:.1f} sq.m smaller than required (200 sq.m)"
                            elif "tutorial" in room_type:
                                if area < 33:
                                    status = f"Invalid. Room is {33 - area:.1f} sq.m smaller than required (33 sq.m)"
                            elif "seminar" in room_type:
                                if area < 132:
                                    status = f"Invalid. Room is {132 - area:.1f} sq.m smaller than required (132 sq.m)"

                        except (ValueError, TypeError):
                            status = f"Invalid format: '{area_val}' is not a number"
                        
                        validation_results.append({
                            "Room Type (3rd Column)": str(df.iloc[row_idx, room_type_col]),
                            "Capacity (4th Column)": area_val,
                            "Status": status
                        })
                
                except Exception as e:
                    print(f"  Error validating sheet '{sheet_name}': {e}")
                    continue
            
            print(f"Total validations: {len(validation_results)}\n")
            return pd.DataFrame(validation_results)
        
        except Exception as e:
            print(f"Error in validate_classroom_details: {e}")
            traceback.print_exc()
            return pd.DataFrame()

    def generate_report(faculty_data, infrastructure_data, validation_results=None, 
                       college_name=None, intake=None, document_completeness=None,
                       faculty_qualification_experience=None, student_faculty_ratio=None,
                       approval_certificate_validity=None, clause_compliance=None,
                       final_compliance_score=None, final_compliance_status=None):
        """
        Generate comprehensive compliance report with AI insights.
        """
        try:
            output_pdf = BytesIO()
            doc = SimpleDocTemplate(output_pdf, pagesize=letter)
            elements = []
            styles = getSampleStyleSheet()

            # Title
            title_style = styles['Title']
            title_style.fontName = 'Helvetica-Bold'
            title_style.fontSize = 16
            title = Paragraph("<b>AICTE Compliance Report</b>", title_style)
            elements.append(title)
            
            # Subtitle
            subtitle = Paragraph(f"<b>Institution:</b> {college_name}<br/><b>Approved Intake:</b> {intake}", 
                                styles['Normal'])
            elements.append(subtitle)
            elements.append(Spacer(1, 12))

            # Note
            note = Paragraph("<i>(Data extracted from mandatory disclosure)</i>", styles['Normal'])
            elements.append(note)
            elements.append(Spacer(1, 20))

            # Faculty Compliance Table
            def create_faculty_table(elements, faculty_data):
                heading = Paragraph("<b>Faculty Compliance Status</b>", styles['Heading2'])
                elements.append(heading)
                elements.append(Spacer(1, 8))
                
                data = [
                    ['Faculty Category', 'Actual', 'Required', 'Compliance'],
                    ['Professor', faculty_data['professors'], 
                     faculty_data['required_professors'], 
                     faculty_data['professor_compliance']],
                    ['Associate Professor', faculty_data['associate_professors'], 
                     faculty_data['required_associate_professors'], 
                     faculty_data['associate_professor_compliance']],
                    ['Assistant Professor', faculty_data['assistant_professors'], 
                     faculty_data['required_assistant_professors'], 
                     faculty_data['assistant_professor_compliance']],
                ]

                table = Table(data, colWidths=[200, 100, 100, 100])
                table.setStyle(TableStyle([
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                    ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
                    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, 0), 11),
                    ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
                    ('BOX', (0, 0), (-1, -1), 1, colors.black),
                    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ]))

                # Highlight non-compliant rows
                for row_idx, row in enumerate(data[1:], start=1):
                    if 'Non-Compliant' in str(row[3]):
                        table.setStyle(TableStyle([
                            ('BACKGROUND', (0, row_idx), (-1, row_idx), colors.lightcoral),
                        ]))
                    else:
                        table.setStyle(TableStyle([
                            ('BACKGROUND', (0, row_idx), (-1, row_idx), colors.lightgreen),
                        ]))

                elements.append(table)

            # Infrastructure Compliance Table
            def create_infrastructure_table(elements, infrastructure_data):
                elements.append(Spacer(1, 20))
                heading = Paragraph("<b>Infrastructure Compliance Status</b>", styles['Heading2'])
                elements.append(heading)
                elements.append(Spacer(1, 8))
                
                data = [
                    ['Infrastructure Category', 'Actual', 'Required', 'Compliance'],
                    ['Classrooms', infrastructure_data['classrooms'], 
                     infrastructure_data['required_classrooms'], 
                     infrastructure_data['classroom_compliance']],
                    ['Laboratories', infrastructure_data['labs'], 
                     infrastructure_data['required_labs'], 
                     infrastructure_data['lab_compliance']],
                    ['Workshops', infrastructure_data['workshops'], 
                     infrastructure_data['required_workshops'], 
                     infrastructure_data['workshop_compliance']],
                    ['Smart Classrooms', infrastructure_data['smart_classrooms'], 
                     infrastructure_data['required_smart_classrooms'], 
                     infrastructure_data['smart_classroom_compliance']],
                ]

                table = Table(data, colWidths=[200, 100, 100, 100])
                table.setStyle(TableStyle([
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                    ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
                    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, 0), 11),
                    ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
                    ('BOX', (0, 0), (-1, -1), 1, colors.black),
                    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ]))

                for row_idx, row in enumerate(data[1:], start=1):
                    if 'Non-Compliant' in str(row[3]):
                        table.setStyle(TableStyle([
                            ('BACKGROUND', (0, row_idx), (-1, row_idx), colors.lightcoral),
                        ]))
                    else:
                        table.setStyle(TableStyle([
                            ('BACKGROUND', (0, row_idx), (-1, row_idx), colors.lightgreen),
                        ]))

                elements.append(table)

            # Classroom Validation Table
            def create_validation_table(elements, validation_results):
                # Always create the section, even if results are empty or all valid
                elements.append(Spacer(1, 20))
                heading = Paragraph("<b>Classroom Space Validation Results</b>", styles['Heading2'])
                elements.append(heading)
                elements.append(Spacer(1, 8))

                if validation_results is None or validation_results.empty:
                    note = Paragraph("<i>No classroom validation data available.</i>", styles['Normal'])
                    elements.append(note)
                    return

                # Show all validation results in table format
                data = [['Room Type', 'Capacity (sq.m)', 'Status']]
                for _, row in validation_results.iterrows():
                    data.append([
                        str(row['Room Type (3rd Column)']), 
                        str(row['Capacity (4th Column)']), 
                        str(row['Status'])
                    ])

                table = Table(data, colWidths=[150, 120, 250])
                table.setStyle(TableStyle([
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                    ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
                    ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, 0), 10),
                    ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
                    ('BOX', (0, 0), (-1, -1), 1, colors.black),
                    ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                    ('FONTSIZE', (0, 1), (-1, -1), 9),
                ]))

                # Alternate row colors
                for row_idx in range(1, len(data)):
                    if row_idx % 2 == 0:
                        table.setStyle(TableStyle([
                            ('BACKGROUND', (0, row_idx), (-1, row_idx), colors.lightgrey),
                        ]))
                    else:
                        table.setStyle(TableStyle([
                            ('BACKGROUND', (0, row_idx), (-1, row_idx), colors.white),
                        ]))

                elements.append(table)

            # Missing Document Checklist Table
            def create_document_checklist_table(elements, document_completeness):
                if document_completeness:
                    elements.append(Spacer(1, 20))
                    heading = Paragraph("<b>Missing Document Checklist</b>", styles['Heading2'])
                    elements.append(heading)
                    elements.append(Spacer(1, 8))

                    data = [['Document Name', 'Status', 'Severity']]
                    for doc in document_completeness.get('documents', []):
                        data.append([
                            doc.get('document_name', 'Unknown'),
                            doc.get('status', 'Unknown'),
                            doc.get('severity', 'Unknown')
                        ])

                    table = Table(data, colWidths=[250, 100, 100])
                    table.setStyle(TableStyle([
                        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                        ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
                        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                        ('FONTSIZE', (0, 0), (-1, 0), 10),
                        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
                        ('BOX', (0, 0), (-1, -1), 1, colors.black),
                        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                    ]))

                    # Color code status cells
                    for row_idx, row in enumerate(data[1:], start=1):
                        status = row[1]
                        if status == 'Present':
                            table.setStyle(TableStyle([
                                ('BACKGROUND', (1, row_idx), (1, row_idx), colors.lightgreen),
                                ('TEXTCOLOR', (1, row_idx), (1, row_idx), colors.black),
                            ]))
                        elif status == 'Missing':
                            table.setStyle(TableStyle([
                                ('BACKGROUND', (1, row_idx), (1, row_idx), colors.lightcoral),
                                ('TEXTCOLOR', (1, row_idx), (1, row_idx), colors.black),
                            ]))

                    elements.append(table)

            # Faculty Qualification & Experience Validation Summary Table
            def create_faculty_validation_table(elements, faculty_qualification_experience):
                if faculty_qualification_experience:
                    elements.append(Spacer(1, 20))
                    heading = Paragraph("<b>Faculty Qualification & Experience Validation Summary</b>", styles['Heading2'])
                    elements.append(heading)
                    elements.append(Spacer(1, 8))

                    data = [['Designation', 'Total Faculty', 'Qualification (Valid/Invalid)', 'Experience (Valid/Invalid)']]
                    for faculty in faculty_qualification_experience:
                        qual_valid = faculty.get('qualification_valid', 'N/A')
                        qual_invalid = faculty.get('qualification_invalid', 'N/A')
                        exp_valid = faculty.get('experience_valid', 'N/A')
                        exp_invalid = faculty.get('experience_invalid', 'N/A')

                        data.append([
                            faculty.get('designation', 'Unknown'),
                            str(faculty.get('total', 0)),
                            f"{qual_valid}/{qual_invalid}",
                            f"{exp_valid}/{exp_invalid}"
                        ])

                    table = Table(data, colWidths=[120, 80, 120, 120])
                    table.setStyle(TableStyle([
                        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                        ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
                        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                        ('FONTSIZE', (0, 0), (-1, 0), 9),
                        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
                        ('BOX', (0, 0), (-1, -1), 1, colors.black),
                        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                    ]))

                    elements.append(table)

            # Student-Faculty Ratio Analysis Table
            def create_student_faculty_ratio_table(elements, student_faculty_ratio):
                if student_faculty_ratio:
                    elements.append(Spacer(1, 20))
                    heading = Paragraph("<b>Student-Faculty Ratio Analysis</b>", styles['Heading2'])
                    elements.append(heading)
                    elements.append(Spacer(1, 8))

                    data = [
                        ['Metric', 'Value', 'Status'],
                        ['Student Count', student_faculty_ratio.get('student_count', 'N/A'), ''],
                        ['Total Faculty', student_faculty_ratio.get('total_faculty', 'N/A'), ''],
                        ['Current Ratio', student_faculty_ratio.get('ratio', 'N/A'), student_faculty_ratio.get('status', 'N/A')],
                        ['Maximum Allowed', student_faculty_ratio.get('required_max_ratio', 'N/A'), '']
                    ]

                    table = Table(data, colWidths=[120, 100, 120])
                    table.setStyle(TableStyle([
                        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                        ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
                        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                        ('FONTSIZE', (0, 0), (-1, 0), 10),
                        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
                        ('BOX', (0, 0), (-1, -1), 1, colors.black),
                        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                    ]))

                    # Color code the ratio status
                    ratio_status = student_faculty_ratio.get('status', 'Not Available')
                    if ratio_status == 'Compliant':
                        table.setStyle(TableStyle([
                            ('BACKGROUND', (2, 3), (2, 3), colors.lightgreen),
                            ('TEXTCOLOR', (2, 3), (2, 3), colors.black),
                        ]))
                    elif ratio_status == 'Warning':
                        table.setStyle(TableStyle([
                            ('BACKGROUND', (2, 3), (2, 3), colors.lightyellow),
                            ('TEXTCOLOR', (2, 3), (2, 3), colors.black),
                        ]))
                    elif ratio_status == 'Non-Compliant':
                        table.setStyle(TableStyle([
                            ('BACKGROUND', (2, 3), (2, 3), colors.lightcoral),
                            ('TEXTCOLOR', (2, 3), (2, 3), colors.black),
                        ]))

                    elements.append(table)

            # Approval / Certificate Expiry Status Table
            def create_certificate_validity_table(elements, approval_certificate_validity):
                if approval_certificate_validity:
                    elements.append(Spacer(1, 20))
                    heading = Paragraph("<b>Approval / Certificate Expiry Status</b>", styles['Heading2'])
                    elements.append(heading)
                    elements.append(Spacer(1, 8))

                    data = [['Document Type', 'Expiry Date', 'Status']]
                    for cert in approval_certificate_validity:
                        data.append([
                            cert.get('document_type', 'Unknown'),
                            cert.get('expiry_date', 'Not Available'),
                            cert.get('status', 'Not Available')
                        ])

                    table = Table(data, colWidths=[150, 100, 100])
                    table.setStyle(TableStyle([
                        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                        ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
                        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                        ('FONTSIZE', (0, 0), (-1, 0), 9),
                        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
                        ('BOX', (0, 0), (-1, -1), 1, colors.black),
                        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                    ]))

                    # Color code status cells
                    for row_idx, row in enumerate(data[1:], start=1):
                        status = row[2]
                        if status == 'Valid':
                            table.setStyle(TableStyle([
                                ('BACKGROUND', (2, row_idx), (2, row_idx), colors.lightgreen),
                                ('TEXTCOLOR', (2, row_idx), (2, row_idx), colors.black),
                            ]))
                        elif status == 'Expiring Soon':
                            table.setStyle(TableStyle([
                                ('BACKGROUND', (2, row_idx), (2, row_idx), colors.lightyellow),
                                ('TEXTCOLOR', (2, row_idx), (2, row_idx), colors.black),
                            ]))
                        elif status == 'Expired':
                            table.setStyle(TableStyle([
                                ('BACKGROUND', (2, row_idx), (2, row_idx), colors.lightcoral),
                                ('TEXTCOLOR', (2, row_idx), (2, row_idx), colors.black),
                            ]))

                    elements.append(table)

            # Clause-Wise Compliance Table
            def create_clause_compliance_table(elements, clause_compliance):
                if clause_compliance:
                    elements.append(Spacer(1, 20))
                    heading = Paragraph("<b>Clause-Wise Compliance Analysis</b>", styles['Heading2'])
                    elements.append(heading)
                    elements.append(Spacer(1, 8))

                    data = [['Clause ID', 'Clause Name', 'Actual/Required', 'Status']]
                    for clause in clause_compliance:
                        actual = clause.get('actual_value', 'N/A')
                        required = clause.get('required_value', 'N/A')
                        data.append([
                            clause.get('clause_id', 'Unknown'),
                            clause.get('clause_name', 'Unknown'),
                            f"{actual} / {required}",
                            clause.get('status', 'Unknown')
                        ])

                    table = Table(data, colWidths=[60, 140, 120, 80])
                    table.setStyle(TableStyle([
                        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                        ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
                        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                        ('FONTSIZE', (0, 0), (-1, 0), 8),
                        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
                        ('BOX', (0, 0), (-1, -1), 1, colors.black),
                        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                    ]))

                    # Color code status cells
                    for row_idx, row in enumerate(data[1:], start=1):
                        status = row[3]
                        if status == 'Compliant':
                            table.setStyle(TableStyle([
                                ('BACKGROUND', (3, row_idx), (3, row_idx), colors.lightgreen),
                                ('TEXTCOLOR', (3, row_idx), (3, row_idx), colors.black),
                            ]))
                        elif status == 'Warning':
                            table.setStyle(TableStyle([
                                ('BACKGROUND', (3, row_idx), (3, row_idx), colors.lightyellow),
                                ('TEXTCOLOR', (3, row_idx), (3, row_idx), colors.black),
                            ]))
                        elif status == 'Non-Compliant':
                            table.setStyle(TableStyle([
                                ('BACKGROUND', (3, row_idx), (3, row_idx), colors.lightcoral),
                                ('TEXTCOLOR', (3, row_idx), (3, row_idx), colors.black),
                            ]))

                    elements.append(table)

            # Final Compliance Score Section
            def create_final_compliance_section(elements, final_compliance_score, final_compliance_status):
                if final_compliance_score is not None and final_compliance_status:
                    elements.append(Spacer(1, 20))
                    heading = Paragraph("<b>Final Compliance Assessment</b>", styles['Heading1'])
                    elements.append(heading)
                    elements.append(Spacer(1, 12))

                    # Create a summary box
                    score_text = f"Final Compliance Score: {final_compliance_score}/100"
                    status_text = f"Overall Status: {final_compliance_status}"

                    score_paragraph = Paragraph(f"<b>{score_text}</b>", styles['Normal'])
                    status_paragraph = Paragraph(f"<b>{status_text}</b>", styles['Normal'])

                    elements.append(score_paragraph)
                    elements.append(Spacer(1, 6))
                    elements.append(status_paragraph)

                    # Add status interpretation
                    elements.append(Spacer(1, 12))
                    interpretation = ""
                    if final_compliance_score >= 85:
                        interpretation = "Excellent compliance level. All major requirements are met."
                    elif final_compliance_score >= 70:
                        interpretation = "Good compliance level with minor issues that need attention."
                    elif final_compliance_score >= 50:
                        interpretation = "Moderate compliance level. Several areas require improvement."
                    else:
                        interpretation = "Poor compliance level. Immediate action required to meet basic requirements."

                    interpretation_paragraph = Paragraph(f"<i>{interpretation}</i>", styles['Normal'])
                    elements.append(interpretation_paragraph)

            # Create tables
            create_faculty_table(elements, faculty_data)
            create_infrastructure_table(elements, infrastructure_data)

            # Add new sections here
            create_document_checklist_table(elements, document_completeness)
            create_faculty_validation_table(elements, faculty_qualification_experience)
            create_student_faculty_ratio_table(elements, student_faculty_ratio)
            create_certificate_validity_table(elements, approval_certificate_validity)
            create_clause_compliance_table(elements, clause_compliance)
            create_final_compliance_section(elements, final_compliance_score, final_compliance_status)

            create_validation_table(elements, validation_results)

            # Prepare validation summary for AI
            validation_summary = ""
            if validation_results is not None and not validation_results.empty:
                invalid_count = len(validation_results[validation_results['Status'].str.contains('Invalid', na=False)])
                validation_summary = f"\n\nClassroom Validation: {invalid_count} rooms do not meet AICTE size requirements."

            # Summaries from processed photos
            missing_documents = []
            if document_completeness and document_completeness.get('documents'):
                for d in document_completeness['documents']:
                    missing_documents.append(f"{d.get('document_name', 'Unknown')} ({d.get('status', 'Unknown')})")

            qualification_summary = []
            experience_summary = []
            if faculty_qualification_experience:
                for f in faculty_qualification_experience:
                    qualification_summary.append(f"{f.get('designation', 'N/A')}: {f.get('qualification_valid', 'N/A')} valid, {f.get('qualification_invalid', 'N/A')} invalid")
                    experience_summary.append(f"{f.get('designation', 'N/A')}: {f.get('experience_valid', 'N/A')} valid, {f.get('experience_invalid', 'N/A')} invalid")

            non_compliant_clauses = []
            if clause_compliance:
                for c in clause_compliance:
                    if c.get('status') and c['status'].lower().strip() != 'compliant':
                        non_compliant_clauses.append(f"{c.get('clause_id', 'N/A')}: {c.get('clause_name', 'Unknown')} - {c.get('status')}")

            # Generate AI insights
            genai.configure(api_key="AIzaSyAogPEvYUJLJokjsV0oz1zl3_L81BKTcAY")

            prompt = f"""
You are an AICTE compliance expert. Analyze this compliance report and provide actionable insights.

INSTITUTION CONTEXT:
- College: {college_name}
- Approved Intake: {intake}

MISSING DOCUMENTS:
- {', '.join(missing_documents) if missing_documents else 'None reported'}

FACULTY QUALIFICATION VALIDATION:
- {'; '.join(qualification_summary) if qualification_summary else 'No data'}

FACULTY EXPERIENCE VALIDATION:
- {'; '.join(experience_summary) if experience_summary else 'No data'}

STUDENT-FACULTY RATIO:
- Student Count: {student_faculty_ratio.get('student_count', 'N/A')}
- Total Faculty: {student_faculty_ratio.get('total_faculty', 'N/A')}
- Ratio: {student_faculty_ratio.get('ratio', 'N/A')} (Required <= {student_faculty_ratio.get('required_max_ratio', 'N/A')})
- Status: {student_faculty_ratio.get('status', 'N/A')}

APPROVAL/CERTIFICATE VALIDITY:
- {'; '.join([f"{c.get('document_type', 'Unknown')} ({c.get('status', 'N/A')})" for c in approval_certificate_validity]) if approval_certificate_validity else 'No data'}

CLAUSE-WISE NON-COMPLIANCE:
- {'; '.join(non_compliant_clauses) if non_compliant_clauses else 'None'}

FINAL COMPLIANCE SUMMARY:
- Final Score: {final_compliance_score if final_compliance_score is not None else 'N/A'}/100
- Final Status: {final_compliance_status if final_compliance_status else 'N/A'}

CLASSROOM VALIDATION SUMMARY:
{validation_summary}

Provide a structured analysis with the following sections exactly in this order:
1. Overall Compliance Status
2. Critical Issues
3. Major Issues
4. Recommendations
5. Priority Actions
6. Final Recommendation

Include references to the provided data points (documents, ratios, clause non-compliance, final score, and status).
Keep the response professional, concise, and actionable. Use markdown headings and bullet points.
"""

            ai_summary = "Unable to generate AI insights"
            # Use correct model names with delay to avoid rate limits
            model_candidates = ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash", "gemini-pro-latest"]
            import time

            for candidate in model_candidates:
                try:
                    print(f"Attempting Gemini model: {candidate}")
                    model = genai.GenerativeModel(candidate)
                    response = model.generate_content(prompt)
                    if response and hasattr(response, 'text') and response.text:
                        ai_summary = response.text
                        print(f"✓ Gemini model '{candidate}' succeeded")
                        break
                except Exception as e:
                    print(f"✗ Gemini model '{candidate}' failed: {str(e)}")
                    if "429" in str(e) or "Too Many Requests" in str(e):
                        print("Rate limit hit, waiting 30 seconds before next attempt...")
                        time.sleep(30)  # Wait to reset rate limit
                    else:
                        time.sleep(2)  # Short delay for other errors

            # Add AI insights to PDF
            elements.append(Spacer(1, 30))
            ai_heading = Paragraph("<b>AI-Generated Compliance Analysis</b>", styles['Heading1'])
            elements.append(ai_heading)
            elements.append(Spacer(1, 12))

            # Convert markdown to PDF paragraphs
            for line in ai_summary.split('\n'):
                if line.strip():
                    # Handle headers
                    if line.startswith('###'):
                        p = Paragraph(f"<b>{line.replace('###', '').strip()}</b>", styles['Heading3'])
                    elif line.startswith('##'):
                        p = Paragraph(f"<b>{line.replace('##', '').strip()}</b>", styles['Heading2'])
                    elif line.startswith('#'):
                        p = Paragraph(f"<b>{line.replace('#', '').strip()}</b>", styles['Heading1'])
                    # Handle bold
                    elif '**' in line:
                        formatted = line.replace('**', '<b>', 1).replace('**', '</b>', 1)
                        p = Paragraph(formatted, styles['Normal'])
                    # Handle bullets
                    elif line.strip().startswith('-'):
                        formatted = '• ' + line.strip()[1:].strip()
                        p = Paragraph(formatted, styles['Normal'])
                    else:
                        p = Paragraph(line, styles['Normal'])
                    
                    elements.append(p)
                    elements.append(Spacer(1, 6))

            # Build PDF
            doc.build(elements)

            # Save to database
            output_pdf.seek(0)
            final_pdf_content = output_pdf.read()
            
            compliance_report = compliancereport(
                college_name=college_name,
                intake=intake
            )
            compliance_report.report_file.put(BytesIO(final_pdf_content), content_type='application/pdf')
            compliance_report.save()

            print("\nCompliance report generated and saved successfully")
            return {"message": "Report generated successfully", "report_id": str(compliance_report.id)}
            
        except Exception as e:
            error_msg = f"{str(e)}\n{traceback.format_exc()}"
            print(f"Error generating report: {error_msg}")
            raise HTTPException(status_code=500, detail=f"Error generating report: {str(e)}")

    # Analyze data
    excel_file.seek(0)
    total_professors, total_associate_professors, total_assistant_professors = analyze_faculty_data(excel_file)

    excel_file.seek(0)
    total_labs, total_classrooms, total_dept_library, workshops, smart_classroom = analyze_infrastructure_data(excel_file)

    excel_file.seek(0)
    validation_results = validate_classroom_details(excel_file)

    excel_file.seek(0)
    faculty_qualification_experience = validate_faculty_qualification_experience(excel_file)

    # Validate intake
    try:
        intake_str = str(info.college_intake).strip()
        student_intake = int(''.join(filter(str.isdigit, intake_str)) or '0')
        if student_intake <= 0:
            raise ValueError("Student intake must be positive")
    except (ValueError, AttributeError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid college intake: {info.college_intake}")
    
    print(f"\nValidated student intake: {student_intake}")

    # Calculate requirements using AICTE norms
    required_profs = math.ceil(student_intake / 180)
    required_assoc = math.ceil(student_intake / 90)
    required_asst = math.ceil(student_intake / 30)
    
    faculty_data = {
        'professors': total_professors,
        'required_professors': required_profs,
        'professor_compliance': 'Compliant' if total_professors >= required_profs else 'Non-Compliant',
        'associate_professors': total_associate_professors,
        'required_associate_professors': required_assoc,
        'associate_professor_compliance': 'Compliant' if total_associate_professors >= required_assoc else 'Non-Compliant',
        'assistant_professors': total_assistant_professors,
        'required_assistant_professors': required_asst,
        'assistant_professor_compliance': 'Compliant' if total_assistant_professors >= required_asst else 'Non-Compliant',
    }

    def calculate_student_faculty_ratio(total_faculty, student_intake):
        """
        Calculate student-faculty ratio and determine compliance status.
        """
        try:
            if total_faculty <= 0:
                return {
                    "student_count": student_intake if student_intake > 0 else "Not Available",
                    "total_faculty": total_faculty,
                    "ratio": "Not Available",
                    "required_max_ratio": 20,
                    "status": "Not Available"
                }
            
            if student_intake <= 0:
                return {
                    "student_count": "Not Available",
                    "total_faculty": total_faculty,
                    "ratio": "Not Available",
                    "required_max_ratio": 20,
                    "status": "Not Available"
                }
            
            ratio = round(student_intake / total_faculty, 2)
            
            if ratio <= 20:
                status = "Compliant"
            elif ratio <= 25:
                status = "Warning"
            else:
                status = "Non-Compliant"
            
            return {
                "student_count": student_intake,
                "total_faculty": total_faculty,
                "ratio": ratio,
                "required_max_ratio": 20,
                "status": status
            }
        except Exception as e:
            print(f"Error calculating student-faculty ratio: {e}")
            return {
                "student_count": "Not Available",
                "total_faculty": "Not Available",
                "ratio": "Not Available",
                "required_max_ratio": 20,
                "status": "Not Available"
            }

    # Calculate student-faculty ratio
    total_faculty_count = total_professors + total_associate_professors + total_assistant_professors
    student_faculty_ratio = calculate_student_faculty_ratio(total_faculty_count, student_intake)

    def check_approval_certificate_validity(college_name, college_intake):
        """
        Check expiry status of approval and certificate documents.
        Placeholder implementation that can be extended with actual document parsing.
        """
        from datetime import datetime, timedelta
        import re
        
        try:
            current_date = datetime.now().date()
            
            # Document types to check
            document_types = [
                "AICTE Approval Letter",
                "University Affiliation Letter", 
                "Fire NOC / Safety Certificate"
            ]
            
            results = []
            
            def parse_date_from_text(text):
                """Extract date from text using regex patterns."""
                if not text or text == "Not Available":
                    return None
                    
                # Common date patterns
                patterns = [
                    r'(\d{1,2})[/-](\d{1,2})[/-](\d{4})',  # DD/MM/YYYY or DD-MM-YYYY
                    r'(\d{4})[/-](\d{1,2})[/-](\d{1,2})',  # YYYY/MM/DD or YYYY-MM-DD
                ]
                
                for pattern in patterns:
                    matches = re.findall(pattern, text)
                    if matches:
                        for match in matches:
                            try:
                                if len(match) == 3:
                                    # Determine format based on first group length
                                    if len(match[0]) == 4:  # YYYY-MM-DD
                                        year, month, day = int(match[0]), int(match[1]), int(match[2])
                                    else:  # DD-MM-YYYY
                                        day, month, year = int(match[0]), int(match[1]), int(match[2])
                                    
                                    # Validate date
                                    if 1 <= month <= 12 and 1 <= day <= 31 and year >= 2000:
                                        return datetime(year, month, day).date()
                            except ValueError:
                                continue
                
                return None
            
            def get_document_status(doc_type):
                """Get expiry status for a document type."""
                try:
                    from institute.models import certificate, supporting_document
                    
                    # Map document types to supporting_document field_name values
                    field_mapping = {
                        "AICTE Approval Letter": ["approval_affiliation", "aicte"],
                        "University Affiliation Letter": ["approval_affiliation"],
                        "Fire NOC / Safety Certificate": ["fire_noc"]
                    }
                    
                    expected_fields = field_mapping.get(doc_type, [])
                    
                    # Check supporting_document first (uploaded docs)
                    supp_docs = list(supporting_document.objects(college_name=college_name))
                    for sup in supp_docs:
                        sup_field = (getattr(sup, "field_name", "") or "").lower()
                        sup_name = (getattr(sup, "name", "") or "").lower()
                        
                        # Check exact field_name match first
                        if sup_field in expected_fields:
                            # Document found - mark as Valid (just uploaded)
                            return {
                                "document_type": doc_type,
                                "expiry_date": "Valid (Recently Uploaded)",
                                "status": "Valid"
                            }
                        
                        # Fallback to name-based matching
                        if any(field in sup_name for field in expected_fields):
                            return {
                                "document_type": doc_type,
                                "expiry_date": "Valid (Recently Uploaded)",
                                "status": "Valid"
                            }
                    
                    # Check certificates collection as fallback
                    certs = list(certificate.objects(college_name=college_name))
                    for cert in certs:
                        cert_name = (getattr(cert, "name", "") or "").lower()
                        cert_field = (getattr(cert, "field_name", "") or "").lower()
                        
                        if cert_field in expected_fields:
                            return {
                                "document_type": doc_type,
                                "expiry_date": "Valid",
                                "status": "Valid"
                            }
                        
                        if any(field in cert_name for field in expected_fields):
                            return {
                                "document_type": doc_type,
                                "expiry_date": "Valid",
                                "status": "Valid"
                            }
                    
                    # Document not found
                    return {
                        "document_type": doc_type,
                        "expiry_date": "Not Available",
                        "status": "Not Available"
                    }
                    
                except Exception as e:
                    print(f"Error checking {doc_type}: {e}")
                    return {
                        "document_type": doc_type,
                        "expiry_date": "Not Available",
                        "status": "Not Available"
                    }
            
            # Check each document type
            for doc_type in document_types:
                result = get_document_status(doc_type)
                results.append(result)
            
            return results
            
        except Exception as e:
            print(f"Error in check_approval_certificate_validity: {e}")
            traceback.print_exc()
            return [
                {"document_type": "AICTE Approval Letter", "expiry_date": "Not Available", "status": "Not Available"},
                {"document_type": "University Affiliation Letter", "expiry_date": "Not Available", "status": "Not Available"},
                {"document_type": "Fire NOC / Safety Certificate", "expiry_date": "Not Available", "status": "Not Available"}
            ]

    # Check approval and certificate validity
    approval_certificate_validity = check_approval_certificate_validity(info.college_name, info.college_intake)

    required_classrooms = math.ceil(student_intake / 60)
    dept = 1  # Assuming one department
    required_labs = 2 * dept * 3

    infrastructure_data = {
        'classrooms': total_classrooms,
        'required_classrooms': required_classrooms,
        'classroom_compliance': 'Compliant' if total_classrooms >= required_classrooms else 'Non-Compliant',
        'labs': total_labs,
        'required_labs': required_labs,
        'lab_compliance': 'Compliant' if total_labs >= required_labs else 'Non-Compliant',
        'workshops': workshops,
        'required_workshops': 1,
        'workshop_compliance': 'Compliant' if workshops >= 1 else 'Non-Compliant',
        'smart_classrooms': smart_classroom,
        'required_smart_classrooms': 4,
        'smart_classroom_compliance': 'Compliant' if smart_classroom >= 4 else 'Non-Compliant',
    }

    # Generate report
    # Build document-image cross validation summary
    image_evidence = get_image_evidence_summary(info.college_name, branch='entc')
    image_document_crosscheck = build_document_image_crosscheck(infrastructure_data, image_evidence)

    # Build clause-wise compliance analysis first to include in PDF
    document_completeness = check_missing_documents(info.college_name, info.college_intake)
    clause_compliance = build_clause_compliance(
        faculty_data, infrastructure_data, student_faculty_ratio,
        faculty_qualification_experience, document_completeness,
        approval_certificate_validity, student_intake
    )

    # Calculate final compliance score
    final_compliance = calculate_final_compliance_score(clause_compliance)

    report_result = generate_report(
        faculty_data,
        infrastructure_data,
        validation_results,
        college_name=info.college_name,
        intake=info.college_intake,
        document_completeness=document_completeness,
        faculty_qualification_experience=faculty_qualification_experience,
        student_faculty_ratio=student_faculty_ratio,
        approval_certificate_validity=approval_certificate_validity,
        clause_compliance=clause_compliance,
        final_compliance_score=final_compliance['final_score'],
        final_compliance_status=final_compliance['final_status']
    )

    # Add qualification/experience validation to response
    report_result['faculty_qualification_experience'] = faculty_qualification_experience
    report_result['student_faculty_ratio'] = student_faculty_ratio
    report_result['approval_certificate_validity'] = approval_certificate_validity

    # Add image/document cross-validation details
    report_result['image_evidence'] = image_evidence
    report_result['image_document_crosscheck'] = image_document_crosscheck

    # Add certificate + document crosscheck summary
    report_result['certificate_crosscheck'] = build_certificate_document_crosscheck(info.college_name, info.college_intake)

    # Add image inspection scoring
    report_result['image_inspection_scores'] = calculate_image_inspection_score(
        image_evidence.get('classroom_entries'),
        image_evidence.get('lab_entries'),
        image_evidence,
        image_document_crosscheck
    )

    # Add to response
    report_result['clause_compliance'] = clause_compliance
    report_result['final_compliance_score'] = final_compliance['final_score']
    report_result['final_compliance_status'] = final_compliance['final_status']

    return report_result


class DeficiencyReportRequest(BaseModel):
    """Request model for deficiency report generation from uploaded images."""
    college_name: str
    branch: str = "entc"


class ScoreAdjustmentRequest(BaseModel):
    """Request model for manually adjusting inspection report scores."""
    college_name: str
    branch: str = "entc"
    image_quality_score: float = None
    classroom_compliance_score: float = None
    lab_compliance_score: float = None
    smart_classroom_score: float = None
    evidence_completeness_score: float = None
    doc_image_consistency_score: float = None
    override_reason: str = "Manual adjustment"


@app.post("/generate-report/")
async def generate_report(info: DeficiencyReportRequest):
    """
    Generate comprehensive facility inspection report from all uploaded images.
    Processes: Classroom, Lab, Canteen, PWD Facilities, Parking, and Washroom images.
    Uses YOLO object detection to verify compliance with facility requirements.
    """
    try:
        print(f"\n{'='*80}")
        print(f"[Auto-Report] Starting comprehensive inspection report generation")
        print(f"College: {info.college_name}, Branch: {info.branch}")
        print(f"{'='*80}\n")
        
        # Query MongoDB for Images document
        document = Images.objects(college=info.college_name)
        if not document:
            raise HTTPException(
                status_code=404, 
                detail=f"No image documents found for college: {info.college_name}"
            )
        
        # Check that both classroom and lab images exist (core requirement)
        has_classroom = False
        has_lab = False
        has_canteen = False
        has_pwd = False
        has_parking = False
        has_washroom = False
        
        for doc in document:
            if doc.classroom and len(doc.classroom) > 0:
                has_classroom = True
            if doc.lab and len(doc.lab) > 0:
                has_lab = True
            if hasattr(doc, 'canteen') and doc.canteen and len(doc.canteen) > 0:
                has_canteen = True
            if hasattr(doc, 'pwd') and doc.pwd and len(doc.pwd) > 0:
                has_pwd = True
            if hasattr(doc, 'parking') and doc.parking and len(doc.parking) > 0:
                has_parking = True
            if hasattr(doc, 'washroom') and doc.washroom and len(doc.washroom) > 0:
                has_washroom = True
        
        if not has_classroom or not has_lab:
            raise HTTPException(
                status_code=400, 
                detail=f"Classroom and Lab images are required. Classroom: {has_classroom}, Lab: {has_lab}"
            )
        
        # Initialize variables
        branch_intake = None
        no_div = None
        no_batches = None
        
        # Initialize URL lists for all categories
        cloudinary_urls = {
            'classroom': [],
            'lab': [],
            'canteen': [],
            'pwd': [],
            'parking': [],
            'washroom': []
        }
        
        # Extract all image URLs and metadata
        for doc in document:
            # Extract metadata from classroom (only once)
            if branch_intake is None and doc.classroom:
                for item in doc.classroom:
                    if item.get('branch') == info.branch or item.get('branch') == "entc":
                        branch_intake = item.get('itbk', 60)
                        no_div = item.get('nod', 1)
                        no_batches = item.get('nob', 1)
                        break
            
            # Extract classroom URLs
            for item in doc.classroom:
                if item.get('branch') == info.branch or item.get('branch') == "entc":
                    url = item.get('url')
                    if url:
                        if isinstance(url, list):
                            cloudinary_urls['classroom'].extend(url)
                        else:
                            cloudinary_urls['classroom'].append(url)
            
            # Extract lab URLs
            for item in doc.lab:
                if item.get('branch') == info.branch or item.get('branch') == "entc":
                    url = item.get('url')
                    if url:
                        if isinstance(url, list):
                            cloudinary_urls['lab'].extend(url)
                        else:
                            cloudinary_urls['lab'].append(url)
            
            # Extract canteen URLs
            if hasattr(doc, 'canteen') and doc.canteen:
                for item in doc.canteen:
                    url = item.get('url') if isinstance(item, dict) else item
                    if url:
                        if isinstance(url, list):
                            cloudinary_urls['canteen'].extend(url)
                        else:
                            cloudinary_urls['canteen'].append(url)
            
            # Extract PWD URLs
            if hasattr(doc, 'pwd') and doc.pwd:
                for item in doc.pwd:
                    url = item.get('url') if isinstance(item, dict) else item
                    if url:
                        if isinstance(url, list):
                            cloudinary_urls['pwd'].extend(url)
                        else:
                            cloudinary_urls['pwd'].append(url)
            
            # Extract parking URLs
            if hasattr(doc, 'parking') and doc.parking:
                for item in doc.parking:
                    url = item.get('url') if isinstance(item, dict) else item
                    if url:
                        if isinstance(url, list):
                            cloudinary_urls['parking'].extend(url)
                        else:
                            cloudinary_urls['parking'].append(url)
            
            # Extract washroom URLs
            if hasattr(doc, 'washroom') and doc.washroom:
                for item in doc.washroom:
                    url = item.get('url') if isinstance(item, dict) else item
                    if url:
                        if isinstance(url, list):
                            cloudinary_urls['washroom'].extend(url)
                        else:
                            cloudinary_urls['washroom'].append(url)
        
        # Validate metadata
        if branch_intake is None:
            print("[Auto-Report] Warning: Could not extract metadata. Using defaults.")
            branch_intake = 60
            no_div = 1
            no_batches = 1
        
        # Log extracted URLs
        for category, urls in cloudinary_urls.items():
            print(f"[Auto-Report] Extracted {len(urls)} {category} URLs")
        print(f"[Auto-Report] Metadata - Intake: {branch_intake}, Divisions: {no_div}, Batches: {no_batches}")
        
        # Calculate dynamic thresholds
        classroom_threshold, lab_threshold = calculate_dynamic_thresholds(
            branch_intake, no_div, no_batches
        )
        print(f"[Auto-Report] Dynamic thresholds - Classrooms: {classroom_threshold} benches, Labs: {lab_threshold} monitors")
        
        # Convert all URLs to binary
        binary_images = {}
        for category, urls in cloudinary_urls.items():
            binary_images[category] = []
            for url in urls:
                binary_url = get_cloudinary_image_as_binary(url)
                if binary_url is not None:
                    binary_images[category].append(binary_url)
        
        # Validate core images
        if not binary_images['classroom'] or not binary_images['lab']:
            print(f"[Auto-Report] Error: Insufficient core images")
            raise HTTPException(
                status_code=400,
                detail=f"Failed to retrieve core images. Classrooms: {len(binary_images['classroom'])}, Labs: {len(binary_images['lab'])}"
            )
        
        # Process all image categories with YOLO
        print(f"[Auto-Report] Processing images with YOLO...")
        classroom_data = process_classroom_images(binary_images['classroom'], classroom_threshold)
        lab_data = process_lab_images(binary_images['lab'], lab_threshold)
        canteen_data = process_canteen_images(binary_images['canteen']) if binary_images['canteen'] else []
        pwd_data = process_pwd_images(binary_images['pwd']) if binary_images['pwd'] else []
        parking_data = process_parking_images(binary_images['parking']) if binary_images['parking'] else []
        washroom_data = process_washroom_images(binary_images['washroom']) if binary_images['washroom'] else []
        
        print(f"[Auto-Report] ✓ Classrooms: {len(classroom_data)} images")
        print(f"[Auto-Report] ✓ Labs: {len(lab_data)} images")
        print(f"[Auto-Report] ✓ Canteen: {len(canteen_data)} images")
        print(f"[Auto-Report] ✓ PWD Facilities: {len(pwd_data)} images")
        print(f"[Auto-Report] ✓ Parking: {len(parking_data)} images")
        print(f"[Auto-Report] ✓ Washroom: {len(washroom_data)} images")
        
        # Build comprehensive image evidence summary
        image_evidence = {
            'classroom_entries': classroom_data,
            'lab_entries': lab_data,
            'classroom_image_count': len(classroom_data),
            'lab_image_count': len(lab_data),
            'classroom_valid_count': sum(1 for e in classroom_data if e.get('compliance') == 'Compliant'),
            'lab_valid_count': sum(1 for e in lab_data if e.get('compliance') == 'Compliant'),
            'smart_classroom_evidence': sum(
                1 for e in classroom_data + lab_data 
                if any(k in e.get('object_counts', {}) and e.get('object_counts', {}).get(k, 0) > 0 
                       for k in ['monitor', 'laptop', 'tv', 'projector'])
            ),
            'status': 'sufficient' if any(e.get('compliance') == 'Compliant' for e in classroom_data + lab_data) else 'partial',
        }
        
        # Calculate inspection scores (focused on core infrastructure)
        final_scores = calculate_image_inspection_score(classroom_data, lab_data, image_evidence=image_evidence)
        print(f"[Auto-Report] Inspection scores calculated: {final_scores}")
        
        # Generate comprehensive PDF report with all categories
        output_pdf = f"{info.college_name}_{info.branch}_inspection_report.pdf"
        print(f"[Auto-Report] Generating comprehensive PDF: {output_pdf}")
        
        generate_pdf(
            classroom_data,
            lab_data,
            output_pdf,
            info.college_name,
            info.branch,
            branch_intake,
            no_div,
            no_batches,
            inspection_scores=final_scores,
            canteen_data=canteen_data,
            pwd_data=pwd_data,
            parking_data=parking_data,
            washroom_data=washroom_data
        )
        print(f"[Auto-Report] PDF generated successfully")
        
        # Read PDF file
        try:
            with open(output_pdf, 'rb') as pdf_file:
                pdf_data = pdf_file.read()
            print(f"[Auto-Report] PDF file size: {len(pdf_data)} bytes")
        except Exception as file_error:
            print(f"[Auto-Report] Error reading PDF: {file_error}")
            raise HTTPException(status_code=500, detail=f"Failed to read PDF file: {str(file_error)}")
        
        # Save to MongoDB
        try:
            deficiency_report_obj = deficiency_report(
                file=pdf_data,
                college=info.college_name,
                branch=info.branch
            )
            deficiency_report_obj.save()
            print(f"[Auto-Report] ✅ Report saved to MongoDB with ID: {deficiency_report_obj.id}")
        except Exception as mongo_error:
            print(f"[Auto-Report] Error saving to MongoDB: {mongo_error}")
            raise HTTPException(status_code=500, detail=f"Failed to save report: {str(mongo_error)}")
        
        # Cleanup temporary file
        try:
            if os.path.exists(output_pdf):
                os.remove(output_pdf)
        except Exception as cleanup_error:
            print(f"[Auto-Report] Warning: Could not clean up temporary file: {cleanup_error}")
        
        print(f"[Auto-Report] ✅ Comprehensive inspection report generation complete!")
        return {
            "message": "Comprehensive inspection report generated successfully",
            "file_id": str(deficiency_report_obj.id),
            "inspection_scores": final_scores,
            "image_counts": {
                "classroom": len(classroom_data),
                "lab": len(lab_data),
                "canteen": len(canteen_data),
                "pwd_facilities": len(pwd_data),
                "parking": len(parking_data),
                "washroom": len(washroom_data),
                "total": len(classroom_data) + len(lab_data) + len(canteen_data) + len(pwd_data) + len(parking_data) + len(washroom_data)
            },
        }
    
    except HTTPException as he:
        print(f"[Auto-Report] ❌ Failed: {he.detail}")
        raise he
    except Exception as e:
        print(f"[Auto-Report] ❌ Unexpected error: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error generating report: {str(e)}")


@app.post("/adjust-report-scores/")
async def adjust_report_scores(adjustment: ScoreAdjustmentRequest):
    """
    🎯 MANUAL SCORE ADJUSTMENT ENDPOINT
    Allows admins to manually adjust inspection report scores if images aren't good.
    
    Usage:
    POST /adjust-report-scores/
    {
        "college_name": "College Name",
        "branch": "entc",
        "image_quality_score": 75,
        "classroom_compliance_score": 80,
        "lab_compliance_score": 85,
        "smart_classroom_score": 70,
        "evidence_completeness_score": 80,
        "doc_image_consistency_score": 65,
        "override_reason": "Images were good but lighting was poor"
    }
    """
    try:
        print(f"\n{'='*80}")
        print(f"[Score Override] Adjusting inspection report scores")
        print(f"College: {adjustment.college_name}, Branch: {adjustment.branch}")
        print(f"Reason: {adjustment.override_reason}")
        print(f"{'='*80}\n")
        
        # Query for existing report
        existing_report = None
        try:
            # Try to find recent report
            existing_report = deficiency_report.objects(
                college=adjustment.college_name,
                branch=adjustment.branch
            ).order_by('-id').first()
        except:
            pass
        
        if not existing_report:
            raise HTTPException(
                status_code=404,
                detail=f"No report found for {adjustment.college_name}. Generate a report first."
            )
        
        # Calculate adjusted final score
        scores = {}
        original_scores = {}
        
        # Collect all provided scores
        if adjustment.image_quality_score is not None:
            scores['image_quality_score'] = adjustment.image_quality_score
        if adjustment.classroom_compliance_score is not None:
            scores['classroom_compliance_score'] = adjustment.classroom_compliance_score
        if adjustment.lab_compliance_score is not None:
            scores['lab_compliance_score'] = adjustment.lab_compliance_score
        if adjustment.smart_classroom_score is not None:
            scores['smart_classroom_score'] = adjustment.smart_classroom_score
        if adjustment.evidence_completeness_score is not None:
            scores['evidence_completeness_score'] = adjustment.evidence_completeness_score
        if adjustment.doc_image_consistency_score is not None:
            scores['doc_image_consistency_score'] = adjustment.doc_image_consistency_score
        
        # Calculate new weighted score with lenient weights
        new_final_score = round(
            0.10 * scores.get('image_quality_score', 50) +
            0.25 * scores.get('classroom_compliance_score', 50) +
            0.25 * scores.get('lab_compliance_score', 50) +
            0.15 * scores.get('smart_classroom_score', 50) +
            0.15 * scores.get('evidence_completeness_score', 50) +
            0.10 * scores.get('doc_image_consistency_score', 50),
            1
        )
        
        # Determine status based on new score (lenient thresholds)
        if new_final_score >= 70:
            new_status = 'Compliant'
        elif new_final_score >= 50:
            new_status = 'Partially Compliant'
        else:
            new_status = 'Non-Compliant'
        
        print(f"[Score Override] Original Final Score: (based on old report)")
        for key, value in scores.items():
            print(f"[Score Override]   {key}: {value}")
        print(f"[Score Override] New Final Score: {new_final_score}%")
        print(f"[Score Override] New Status: {new_status}")
        print(f"[Score Override] Override Reason: {adjustment.override_reason}")
        
        return {
            "message": "Scores adjusted successfully",
            "college_name": adjustment.college_name,
            "branch": adjustment.branch,
            "adjusted_scores": scores,
            "final_overall_score": new_final_score,
            "final_overall_status": new_status,
            "override_reason": adjustment.override_reason,
            "note": "To apply these scores to the report, download a new report from dashboard"
        }
    
    except HTTPException as he:
        print(f"[Score Override] ❌ Failed: {he.detail}")
        raise he
    except Exception as e:
        print(f"[Score Override] ❌ Unexpected error: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error adjusting scores: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8001)