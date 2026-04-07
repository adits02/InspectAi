import os
import re
import logging
from datetime import datetime
from typing import Dict, List, Any

import pytesseract
from PyPDF2 import PdfReader
from pdf2image import convert_from_path
from fuzzywuzzy import fuzz

# Set Poppler path for pdf2image
os.environ['PATH'] += r";C:\Users\ACER\Downloads\Release-25.12.0-0\poppler-25.12.0\Library\bin"

# Configure logging
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s: %(message)s',
    filename='certificate_verification.log'
)
logger = logging.getLogger(__name__)

class CertificateVerifier:
    def __init__(self, tesseract_path: str = r"C:\Program Files\Tesseract-OCR\tesseract.exe", format_threshold: int = 70, regulatory_threshold: int = 80, regulatory_formats: Dict[str, List[str]] = None):
        """
        Initialize the Certificate Verifier
        
        :param tesseract_path: Path to Tesseract OCR executable
        :param format_threshold: Percent threshold for considering a format a match
        :param regulatory_threshold: Per-element percent to consider an expected regulatory element present
        :param regulatory_formats: Optional override for regulatory element lists
        """
        pytesseract.pytesseract.tesseract_cmd = tesseract_path
        
        # Configuration for certificate types
        self.certificate_types = [
            "Certificate of Architecture",
            "Certificate of Bank Manager",
            "Certificate of Advocate",
            "Academic Certificate",
            "Professional Certificate"
        ]

        # Scoring thresholds and regulatory formats (configurable)
        self.format_threshold = format_threshold  # percent required for format match
        self.regulatory_threshold = regulatory_threshold  # per-element threshold to consider an element present

        # Required certificate names the project expects
        self.required_certificates = [
            'Anti-Ragging Committee Certificate',
            'Internal Committee Certificate',
            'Annual IC Report',
            'SC/ST Committee Certificate',
            'Institution’s Innovation Council (IIC) Certificate',
            'Academic Bank of Credit (ABC) Compliance',
            'Digital Transactions Certificate',
            'Mental Health Counselling Center Certificate',
            'Internal Assessment and Laboratory Work Compliance Certificate',
            'Fire and Life Safety Certificate',
            'Approved Plan and Occupancy Certificate',
            'Audited Financial Statement',
            'Certificate of Advocate',
            'Certificate of Architect Registered with Council of Architecture',
            'Certificate of the Bank Manager',
            'Certificate of Incorporation',
            'Occupancy/Completion/Building License Certificate/Form D',
            'Certificate Regarding Minority Status',
            'Certificate by an Architect',
            'Structural Stability Certificate',
            'Undertaking by the Institute'
        ]

        # Expected keywords/elements for regulatory formats and certificate profiles (case-insensitive)
        self.regulatory_formats = regulatory_formats or {
            'AICTE': [
                'aicte', 'approval', 'approval no', 'approval number', 'affiliation',
                'affiliated', 'principal', 'institute', 'course', 'date'
            ],
            'NAAC': [
                'naac', 'grade', 'accreditation', 'valid upto', 'validity period', 'institution'
            ],
            # Generic profiles for the set of required documents
            'Anti-Ragging Committee Certificate': ['anti-ragging', 'affidavit', 'committee', 'undertaking'],
            'Internal Committee Certificate': ['internal', 'committee', 'anti-ragging', 'member'],
            'Annual IC Report': ['annual', 'report', 'internal committee', 'activity'],
            'SC/ST Committee Certificate': ['sc/st', 'sc st', 'committee', 'reservation'],
            'Institution’s Innovation Council (IIC) Certificate': ['iic', 'innovation', 'council', 'institution'],
            'Academic Bank of Credit (ABC) Compliance': ['academic bank', 'abc', 'credit', 'compliance'],
            'Digital Transactions Certificate': ['digital', 'transactions', 'payment', 'cashless'],
            'Mental Health Counselling Center Certificate': ['mental', 'counselling', 'counseling', 'health', 'center'],
            'Internal Assessment and Laboratory Work Compliance Certificate': ['internal assessment', 'laboratory', 'lab', 'compliance'],
            'Fire and Life Safety Certificate': ['fire', 'life safety', 'n fire', 'extinguisher', 'evacuation'],
            'Approved Plan and Occupancy Certificate': ['approved plan', 'occupancy', 'plan', 'approved'],
            'Audited Financial Statement': ['audited', 'financial statement', 'auditor', 'audit'],
            'Certificate of Advocate': ['advocate', 'bar council', 'registration'],
            'Certificate of Architect Registered with Council of Architecture': ['architect', 'council of architecture', 'registration'],
            'Certificate of the Bank Manager': ['bank', 'manager', 'bank manager', 'branch'],
            'Certificate of Incorporation': ['incorporation', 'company', 'incorporated', 'registration'],
            'Occupancy/Completion/Building License Certificate/Form D': ['occupancy', 'completion', 'building license', 'form d'],
            'Certificate Regarding Minority Status': ['minority', 'minority status', 'community'],
            'Certificate by an Architect': ['architect', 'certificate by', 'structural'],
            'Structural Stability Certificate': ['structural stability', 'stability', 'structural'],
            'Undertaking by the Institute': ['undertaking', 'institute', 'undertakes']
        }

        # Expected authorities per document type
        self.authority_keywords = {
            'AICTE': ['aicte', 'all india council for technical education', 'approval'],
            'NAAC': ['naac', 'national assessment and accreditation council', 'accreditation'],
            'Fire and Life Safety Certificate': ['fire', 'safety', 'fire department', 'municipality'],
            'Approved Plan and Occupancy Certificate': ['municipal', 'building authority', 'town planning', 'zonal commissioner'],
            'Structural Stability Certificate': ['structural stability', 'chartered structural engineer', 'structure consultant'],
            'Certificate of Architect Registered with Council of Architecture': ['council of architecture', 'coa', 'architect'],
            'Certificate of Advocate': ['bar council', 'bar council of india', 'advocate'],
            'Certificate of the Bank Manager': ['bank', 'manager', 'branch', 'branch manager'],
            'Internal Committee Certificate': ['internal committee', 'institute', 'principal']
        }

        # Configurable institutional metadata to verify membership
        self.institution_metadata_fields = {
            'institute_name': '',
            'branch_name': '',
            'city': ''
        }

    def extract_text_from_pdf(self, pdf_path: str) -> str:
        """
        Extract text from PDF using multiple methods
        
        :param pdf_path: Path to PDF file
        :return: Extracted text
        """
        try:
            reader = PdfReader(pdf_path)
            extracted_text = ""

            for page in reader.pages:
                # Try direct text extraction
                page_text = page.extract_text() or ""

                # If no text, convert page to image and use OCR
                if not page_text.strip():
                    try:
                        images = convert_from_path(pdf_path)
                        for img in images:
                            page_text += pytesseract.image_to_string(img)
                    except Exception as img_error:
                        logger.warning(f"Image-based text extraction failed: {img_error}")

                extracted_text += page_text

            return extracted_text.strip()

        except Exception as e:
            logger.error(f"Text extraction failed: {e}")
            return ""

    def get_user_format(self, certificate_type: str, format_pdf_path: str = None, interactive: bool = True) -> str:
        """
        Get the expected format for a specific certificate type.
        Optionally, extract the format from a provided PDF file.
        
        :param certificate_type: Type of certificate
        :param format_pdf_path: Path to the PDF containing the format
        :return: Expected format as a string
        """
        if format_pdf_path:
            try:
                extracted_format = self.extract_text_from_pdf(format_pdf_path)
                if extracted_format:
                    logger.info(f"Extracted format from PDF: {extracted_format}")
                    return extracted_format
                else:
                    logger.warning("No text could be extracted from the format PDF.")
            except Exception as e:
                logger.error(f"Error reading format PDF: {e}")
        # Non-interactive fallback using sensible defaults
        example_formats = {
            "Certificate of Architecture": "Name, Degree, Institution, Date",
            "Certificate of Bank Manager": "Name, Position, Bank Name, Date",
            "Certificate of Advocate": "Name, Bar Council Registration, Date",
            "Academic Certificate": "Name, Degree, Major, University, Graduation Date",
            "Professional Certificate": "Name, Certification, Issuing Body, Date"
        }

        if not interactive:
            return example_formats.get(certificate_type, 'Name, Details, Date')

        # Fallback to manual input if interactive
        print(f"\nDetected Certificate Type: {certificate_type}")
        print("Please provide the expected format for this certificate.")
        print("Example formats:")
        print(f"Suggested format for {certificate_type}: {example_formats.get(certificate_type, 'Name, Details, Date')}")

        while True:
            user_format = input("Enter expected format (comma-separated elements): ").strip()
            
            # Validate format input
            if user_format and ',' in user_format:
                return user_format
            else:
                print("Invalid format. Please use comma-separated elements.")

    def compare_certificate_format(self, extracted_text: str, expected_format: str) -> Dict[str, float]:
        """
        Compare extracted text with expected format
        
        :param extracted_text: Text extracted from certificate
        :param expected_format: User-provided expected format
        :return: Detailed format matching results
        """
        # Clean and prepare data
        format_elements = [elem.strip().lower() for elem in expected_format.split(',')]

        # Perform detailed analysis
        format_analysis = {
            'overall_similarity': 0.0,
            'element_matches': {}
        }

        # Convert extracted text to lowercase for case-insensitive matching
        cleaned_text = extracted_text.lower()

        # Check each format element
        for element in format_elements:
            # Fuzzy matching for each element
            match_ratio = fuzz.partial_ratio(element, cleaned_text)
            format_analysis['element_matches'][element] = match_ratio

        # Calculate overall similarity
        format_analysis['overall_similarity'] = sum(
            format_analysis['element_matches'].values()
        ) / len(format_elements)

        return format_analysis

    def validate_regulatory_format(self, extracted_text: str, regulatory: str) -> Dict[str, Any]:
        """
        Validate whether the extracted text follows a regulatory format (e.g., AICTE, NAAC).
        Returns element-level fuzzy scores and an overall presence percentage.
        """
        import re

        reg = (regulatory or '').upper()
        elements = self.regulatory_formats.get(reg, [])
        element_matches = {}
        present_count = 0
        cleaned_text = extracted_text.lower()

        for element in elements:
            score = fuzz.partial_ratio(element.lower(), cleaned_text)
            element_matches[element] = score
            if score >= self.regulatory_threshold:
                present_count += 1

        overall_presence = (present_count / len(elements)) * 100 if elements else 0.0

        # Approval number extraction/validation for AICTE (basic patterns)
        approval_info = {'value': None, 'valid': False}
        if reg == 'AICTE':
            # Look for lines like 'AICTE Approval No: AICTE/2025/1234' or 'Approval No. : 1234'
            identifiers = self.extract_identifiers(extracted_text, 'AICTE')
            if identifiers.get('identifiers') and 'approval_number' in identifiers['identifiers']:
                val = identifiers['identifiers']['approval_number']
                approval_info['value'] = val
                if re.search(r"\d", val) or re.search(r"AICTE", val, re.I):
                    approval_info['valid'] = True
            else:
                match = re.search(r"approval\s*(?:no(?:\.|\s*number)?)[\s:\-]*([A-Za-z0-9\/\-\s]+)", extracted_text, re.I)
                if match:
                    val = match.group(1).strip()
                    approval_info['value'] = val
                    # Basic heuristic: must contain some digits and/or 'AICTE' token
                    if re.search(r"\d", val) or re.search(r"AICTE", val, re.I):
                        approval_info['valid'] = True

        # If regulatory requires approval and none found/valid, require it to pass
        requires_approval = any('approval' in e.lower() for e in elements)
        if requires_approval and reg == 'AICTE':
            passed = (overall_presence >= 70) and approval_info['valid']
        else:
            passed = overall_presence >= 70

        return {
            'regulatory': reg,
            'pass': passed,
            'overall_presence': overall_presence,
            'element_matches': element_matches,
            'approval': approval_info
        }

    def validate_against_profile(self, extracted_text: str, profile_name: str) -> Dict[str, Any]:
        """
        Validate extracted text against a named certificate/profile.
        Returns presence percentage and element-level scores.
        """
        profile = (profile_name or '').strip()
        elements = self.regulatory_formats.get(profile, [])
        element_matches = {}
        present_count = 0
        cleaned_text = extracted_text.lower()

        for element in elements:
            score = fuzz.partial_ratio(element.lower(), cleaned_text)
            element_matches[element] = score
            if score >= self.regulatory_threshold:
                present_count += 1

        overall_presence = (present_count / len(elements)) * 100 if elements else 0.0
        passed = overall_presence >= 70

        return {
            'profile': profile,
            'pass': passed,
            'overall_presence': overall_presence,
            'element_matches': element_matches
        }

    def validate_required_certificates_in_dir(self, directory: str, metadata_fields: Dict[str, str] = None) -> Dict[str, Any]:
        """
        Scan a directory for PDF files and attempt to match/validate the required certificates.
        
        Returns a comprehensive structured report containing:
        - certificates: Per-certificate validation results
        - duplicates: Duplicate detection information
        - institution_summary: Overall statistics
        - missing_certificates: List of missing certificate names
        - expired_certificates: List of expired certificate names
        - weak_certificates: List of weak/uncertain certificate names
        - duplicate_certificates: List of duplicate certificate details
        - certificate_compliance_score: Overall compliance percentage (0-100)
        - recommendations: Per-certificate recommendations for missing/expired/weak certificates
        """
        import glob

        report = {}
        pdf_files = glob.glob(os.path.join(directory, '**', '*.pdf'), recursive=True)

        # Cache extracted text to avoid repeated OCR
        text_cache = {}

        # Use provided metadata or defaults
        metadata_fields = metadata_fields or self.institution_metadata_fields

        all_candidates = []

        primary_assignment = {}
        request_report = {}

        for req in self.required_certificates:
            candidate_entries = []

            for pdf in pdf_files:
                filename = os.path.basename(pdf)
                filename_lower = filename.lower()
                name_score = fuzz.partial_ratio(req.lower(), filename_lower)

                if pdf not in text_cache:
                    text_cache[pdf] = self.extract_text_from_pdf(pdf)

                extracted_text = text_cache[pdf] or ''
                validation = self.validate_against_profile(extracted_text, req)
                content_score = validation.get('overall_presence', 0)
                combined_score = round(0.45 * name_score + 0.55 * content_score, 1)

                metadata_result = self.match_institution_metadata(extracted_text, metadata_fields)
                identifier_result = self.extract_identifiers(extracted_text, req)
                authority_info = self.extract_authority_details(extracted_text, req)
                validity_status = self.determine_validity_status(req, self.extract_dates_from_text(extracted_text))

                score_info = self.score_certificate_authenticity(
                    text_extraction=bool(extracted_text),
                    detection_status=self._detect_certificate_type(pdf, extracted_text)['detection_status'],
                    format_similarity=combined_score,
                    regulatory_check={'pass': validation.get('pass', False), 'overall_presence': validation.get('overall_presence', 0)},
                    authority_match=authority_info['authority_match'],
                    identifier_valid=identifier_result.get('identifier_valid', False),
                    metadata_match=metadata_result.get('metadata_match', False),
                    validity_status=validity_status,
                    duplicate_status=None
                )

                candidate_entry = {
                    'path': pdf,
                    'filename': filename,
                    'filename_score': name_score,
                    'content_score': content_score,
                    'combined_score': combined_score,
                    'metadata_score': metadata_result['metadata_score'],
                    'metadata_details': metadata_result['metadata_details'],
                    'validation': validation,
                    'detected_type': self._detect_certificate_type(pdf, extracted_text)['detected_type'],
                    'authority_match': authority_info['authority_match'],
                    'authority_details': authority_info['authority_details'],
                    'identifier_value': identifier_result.get('identifier_value'),
                    'identifier_valid': identifier_result.get('identifier_valid'),
                    'identifiers': identifier_result.get('identifiers'),
                    'validity_status': validity_status,
                    'expiry_flag': 'expired' if validity_status == 'Expired' else ('expiring_soon' if validity_status == 'Expiring Soon' else 'valid' if validity_status == 'Valid' else 'unknown'),
                    'authenticity_score': score_info['score'],
                    'final_status': score_info['final_status'],
                    'auth_reasons': score_info['reasons'],
                    'auth_recommendations': score_info['recommendations'],
                    'duplicate_status': None
                }

                candidate_entries.append(candidate_entry)
                all_candidates.append(candidate_entry)

            candidate_entries.sort(key=lambda x: x['combined_score'], reverse=True)

            best_match = candidate_entries[0] if candidate_entries else None
            alternate_matches = candidate_entries[1:] if len(candidate_entries) > 1 else []

            found = best_match is not None
            status = 'Missing'
            if found:
                if best_match['combined_score'] >= 85:
                    status = 'Found and Valid'
                elif best_match['combined_score'] >= 70:
                    status = 'Found but Weak Match'
                elif best_match['combined_score'] >= 50:
                    status = 'Uncertain'
                else:
                    status = 'Weak Evidence'

                primary_assignment.setdefault(best_match['path'], []).append(req)

            request_report[req] = {
                'found': found,
                'status': status,
                'best_match': best_match,
                'alternate_matches': alternate_matches,
                'duplicate_suspicion': False,
                'validity_status': best_match['validity_status'] if best_match else 'Date Not Found',
                'expiry_status': best_match['expiry_flag'] if best_match else 'unknown',
                'authority_validation': best_match['authority_match'] if best_match else False,
                'identifier_validation': best_match['identifier_valid'] if best_match else False
            }

        duplicate_info = self.detect_duplicates_in_candidates(all_candidates)

        # Mark ambiguous assignment where a single file is best for multiple certificate types
        for path, reqs in primary_assignment.items():
            if len(reqs) > 1:
                for req in reqs:
                    request_report[req]['status'] = 'Ambiguous'
                    request_report[req]['duplicate_suspicion'] = True
                    if request_report[req]['best_match']:
                        request_report[req]['best_match']['duplicate_status'] = 'ambiguous'

        # Mark duplicate suspicion from duplicate detection
        duplicate_path_status = {}
        for dup in duplicate_info.get('duplicates', []):
            if dup.get('duplicate_type') == 'certificate_number':
                duplicate_path_status[dup['path']] = dup['status']
            elif dup.get('duplicate_type') == 'similarity':
                duplicate_path_status[dup['path_a']] = dup['status']
                duplicate_path_status[dup['path_b']] = dup['status']

        for req, entry in request_report.items():
            best = entry.get('best_match')
            if best and best.get('path') in duplicate_path_status:
                entry['duplicate_suspicion'] = True
                entry['best_match']['duplicate_status'] = duplicate_path_status[best['path']]

        total_required = len(self.required_certificates)
        found = sum(1 for e in request_report.values() if e['found'])
        missing = total_required - found
        expired = sum(1 for e in request_report.values() if e.get('validity_status') == 'Expired')
        weak_evidence = sum(1 for e in request_report.values() if e.get('status') in ['Weak Evidence', 'Found but Weak Match', 'Uncertain'])
        duplicates_count = len(duplicate_info.get('duplicates', []))

        compliance_numerator = sum((e['best_match']['authenticity_score'] if e['found'] and e['best_match'] else 0) for e in request_report.values())
        compliance_percent = round(compliance_numerator / total_required, 1) if total_required else 0.0

        institution_summary = {
            'total_required': total_required,
            'found': found,
            'missing': missing,
            'expired': expired,
            'weak_evidence': weak_evidence,
            'duplicates': duplicates_count,
            'overall_certificate_compliance_percent': compliance_percent
        }

        # Build additional structured outputs for API/report readiness
        missing_certificates = [req for req, entry in request_report.items() if not entry['found']]
        expired_certificates = [req for req, entry in request_report.items() if entry.get('validity_status') == 'Expired']
        weak_certificates = [req for req, entry in request_report.items() if entry.get('status') in ['Weak Evidence', 'Found but Weak Match', 'Uncertain']]
        duplicate_certificates = duplicate_info.get('duplicates', [])

        # Generate recommendations per certificate
        recommendations = {}
        for req, entry in request_report.items():
            recs = []
            if not entry['found']:
                recs.append("Certificate is missing - obtain and submit the required document.")
            elif entry.get('validity_status') == 'Expired':
                recs.append("Certificate has expired - renew and submit updated version.")
            elif entry.get('status') in ['Weak Evidence', 'Found but Weak Match', 'Uncertain']:
                recs.append("Certificate match is weak - verify document authenticity and content.")
                if entry.get('authority_validation') == False:
                    recs.append("Issuing authority not clearly identified - confirm authority details.")
                if entry.get('identifier_validation') == False:
                    recs.append("Critical identifiers (approval numbers, etc.) missing or invalid.")
            if entry.get('duplicate_suspicion'):
                recs.append("Potential duplicate certificate detected - review and remove duplicates.")
            recommendations[req] = recs

        return {
            'certificates': request_report,
            'duplicates': duplicate_info,
            'institution_summary': institution_summary,
            'missing_certificates': missing_certificates,
            'expired_certificates': expired_certificates,
            'weak_certificates': weak_certificates,
            'duplicate_certificates': duplicate_certificates,
            'certificate_compliance_score': compliance_percent,
            'recommendations': recommendations
        }

    def _parse_date_string(self, date_string: str) -> Any:
        """Attempt to normalize a date string to datetime.date."""
        from datetime import datetime

        date_string = date_string.strip().replace('.', '/').replace('-', '/').strip()
        # Clean trailing words like 'st', 'nd', 'rd', 'th'
        date_string = re.sub(r'(?i)\b(\d{1,2})(st|nd|rd|th)\b', r'\1', date_string)

        date_formats = [
            '%d/%m/%Y', '%d/%m/%y', '%Y/%m/%d', '%d %b %Y', '%d %B %Y', '%b %d %Y', '%B %d %Y',
            '%d/%m/%Y', '%m/%d/%Y', '%d.%m.%Y', '%d-%m-%Y', '%Y-%m-%d', '%d %m %Y'
        ]
        for fmt in date_formats:
            try:
                parsed = datetime.strptime(date_string, fmt).date()
                return parsed
            except Exception:
                continue

        # Fallback: digits-only 8 chars
        candidates = re.findall(r'(\d{2}[\/\-]\d{2}[\/\-]\d{2,4})', date_string)
        for c in candidates:
            for fmt in ['%d/%m/%Y', '%d/%m/%y', '%m/%d/%Y', '%m/%d/%y']:
                try:
                    parsed = datetime.strptime(c, fmt).date()
                    return parsed
                except Exception:
                    continue

        return None

    def extract_dates_from_text(self, extracted_text: str) -> Dict[str, Any]:
        """Extract issue and validity dates heuristically from certificate text."""
        import re

        text = ' '.join(extracted_text.split())

        mapping = {
            'issue_date': r'(?:issue date|issued on|date of issue)\s*[:\-]?\s*([\w\-/., ]{6,40})',
            'valid_from': r'(?:valid from|effective from|from date)\s*[:\-]?\s*([\w\-/., ]{6,40})',
            'valid_upto': r'(?:valid upto|valid until|valid through|expiry date|expiry|validity upto|validity until)\s*[:\-]?\s*([\w\-/., ]{6,40})',
            'expiry_date': r'(?:expiry date|expiry|expire on|expires on|expiration date)\s*[:\-]?\s*([\w\-/., ]{6,40})'
        }

        parsed = {'issue_date': None, 'valid_from': None, 'valid_upto': None, 'expiry_date': None}

        for key, pattern in mapping.items():
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                candidate = match.group(1).strip()
                date_val = self._parse_date_string(candidate)
                if date_val:
                    parsed[key] = date_val

        # Fallback: try to capture any bare date if not found for key fields
        if not any(parsed.values()):
            date_candidates = re.findall(r'(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}|\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{2,4})', text, re.IGNORECASE)
            if date_candidates:
                first_date = self._parse_date_string(date_candidates[0])
                if first_date:
                    parsed['issue_date'] = first_date

        return parsed

    def determine_validity_status(self, certificate_type: str, dates: Dict[str, Any]) -> str:
        """Classify certificate validity status based on extracted dates."""
        from datetime import datetime, timedelta

        current_date = datetime.now().date()
        valid_until = dates.get('valid_upto') or dates.get('expiry_date')
        issue_date = dates.get('issue_date')

        expiry_delay = timedelta(days=30)

        if not valid_until and not issue_date:
            return 'Date Not Found'

        if valid_until:
            if valid_until < current_date:
                return 'Expired'
            elif valid_until <= current_date + expiry_delay:
                return 'Expiring Soon'
            else:
                return 'Valid'

        # If only issue_date exists, provide a partial hint
        if issue_date:
            return 'Only Issue Date Found'

        return 'Date Not Found'

    def score_certificate_authenticity(
        self,
        text_extraction: bool,
        detection_status: str,
        format_similarity: float,
        regulatory_check: Dict[str, Any],
        authority_match: bool,
        identifier_valid: bool,
        metadata_match: bool,
        validity_status: str,
        duplicate_status: str = None
    ) -> Dict[str, Any]:
        """Compute composite authenticity moving from evidence to overall status."""
        score = 0
        reasons = []

        # 1) Text extraction
        if text_extraction:
            score += 15
        else:
            reasons.append('Text extraction failed')

        # 2) Profile match
        if detection_status == 'Found and Valid':
            score += 15
        elif detection_status == 'Found but Weak Match':
            score += 10
            reasons.append('Profile match weak')
        elif detection_status == 'Uncertain':
            score += 5
            reasons.append('Profile match uncertain')
        else:
            reasons.append('Profile not detected')

        # 3) Regulatory/profile keyword presence
        if regulatory_check:
            if regulatory_check.get('pass'):
                score += 10
            else:
                score += 5
                reasons.append('Regulatory check failing')
        else:
            score += 5
            reasons.append('Regulatory not checked')

        # 4) Authority match
        if authority_match:
            score += 10
        else:
            reasons.append('Issuing authority not matched')

        # 5) ID/approval presence
        if identifier_valid:
            score += 10
        else:
            reasons.append('Critical identifier missing or invalid')

        # 6) Institute metadata match
        if metadata_match:
            score += 10
        else:
            reasons.append('Institute metadata mismatch')

        # 7) Date validity
        if validity_status == 'Valid':
            score += 15
        elif validity_status == 'Expiring Soon':
            score += 10
            reasons.append('Validity expiring soon')
        elif validity_status == 'Only Issue Date Found':
            score += 5
            reasons.append('Only issue date found')
        elif validity_status == 'Date Not Found':
            reasons.append('Validity date not found')
        elif validity_status == 'Expired':
            score += 0
            reasons.append('Certificate expired')

        # 8) Duplicate detection impact
        if duplicate_status == 'duplicate_confirmed':
            score -= 20
            reasons.append('Confirmed duplicate certificate')
        elif duplicate_status == 'duplicate_suspected':
            score -= 10
            reasons.append('Suspected duplicate certificate')

        score = max(0, min(100, score))

        final_status = 'Invalid'
        if validity_status == 'Expired':
            final_status = 'Expired'
        elif not text_extraction:
            final_status = 'Missing'
        elif score >= 80:
            final_status = 'Valid'
        elif score >= 60:
            final_status = 'Partially Valid'
        elif score >= 40:
            final_status = 'Weak Evidence'
        else:
            final_status = 'Invalid'

        recommendations = []
        if not text_extraction:
            recommendations.append('Re-scan or provide better quality PDF.')
        if not authority_match:
            recommendations.append('Confirm issuing authority label on certificate.')
        if not identifier_valid:
            recommendations.append('Include/verify registration or approval number.')
        if not metadata_match:
            recommendations.append('Verify institute/branch/city details in certificate text.')
        if validity_status == 'Expired':
            recommendations.append('Certificate may be outdated; acquire a valid one.')
        if validity_status == 'Date Not Found':
            recommendations.append('Locate validity/expiry date in certificate')
        if duplicate_status is not None:
            recommendations.append('Resolve duplicate certificate files in submission.')

        return {
            'score': score,
            'final_status': final_status,
            'reasons': reasons,
            'recommendations': recommendations
        }

    def extract_authority_details(self, extracted_text: str, certificate_type: str) -> Dict[str, Any]:
        """Identify and validate issuing authority markers in certificate text."""
        import re

        text = extracted_text.lower()
        keywords = self.authority_keywords.get(certificate_type, [])
        found = []

        for kw in keywords:
            if kw in text:
                found.append(kw)

        authority_match = len(found) > 0

        return {
            'authority_match': authority_match,
            'authority_details': found
        }

    def extract_identifiers(self, extracted_text: str, certificate_type: str) -> Dict[str, Any]:
        """Extract approval, registration, license, architect/bar details from text."""
        import re

        text = extracted_text

        patterns = {
            'approval_number': r'(?:approval\s*(?:no\.?|number)\s*[:\-]?\s*([A-Za-z0-9\/\-]+))',
            'registration_number': r'(?:registration\s*(?:no\.?|number)\s*[:\-]?\s*([A-Za-z0-9\/\-]+))',
            'license_number': r'(?:license\s*(?:no\.?|number|#)\s*[:\-]?\s*([A-Za-z0-9\/\-]+))',
            'architect_registration_number': r'(?:architect.*registration\s*(?:no\.?|number)\s*[:\-]?\s*([A-Za-z0-9\/\-]+))',
            'bar_council_registration_number': r'(?:bar\s*council.*registration\s*(?:no\.?|number)\s*[:\-]?\s*([A-Za-z0-9\/\-]+))',
            'bank_branch_details': r'(?:branch\s*(?:name|no|number|code)\s*[:\-]?\s*([A-Za-z0-9 \,\-\/]+))'
        }

        result = {
            'identifier_found': False,
            'identifier_value': None,
            'identifier_valid': False,
            'identifiers': {}
        }

        for key, pat in patterns.items():
            match = re.search(pat, text, re.IGNORECASE)
            if match:
                value = match.group(1).strip()
                if value:
                    result['identifiers'][key] = value

        if result['identifiers']:
            result['identifier_found'] = True

            # For required checks by certificate type
            if certificate_type == 'AICTE':
                result['identifier_valid'] = 'approval_number' in result['identifiers']
            elif certificate_type == 'Certificate of Architect Registered with Council of Architecture':
                result['identifier_valid'] = 'architect_registration_number' in result['identifiers']
            elif certificate_type == 'Certificate of Advocate':
                result['identifier_valid'] = 'bar_council_registration_number' in result['identifiers']
            elif certificate_type == 'Certificate of the Bank Manager':
                result['identifier_valid'] = 'bank_branch_details' in result['identifiers']
            else:
                # fallback for any registration/license number to claim valid
                result['identifier_valid'] = any(k in result['identifiers'] for k in ['registration_number', 'license_number', 'approval_number'])

            # Pick highest priority identifier value
            for primary in ['approval_number', 'registration_number', 'architect_registration_number', 'bar_council_registration_number', 'license_number', 'bank_branch_details']:
                if primary in result['identifiers']:
                    result['identifier_value'] = result['identifiers'][primary]
                    break

        return result

    def match_institution_metadata(self, extracted_text: str, metadata_fields: Dict[str, str]) -> Dict[str, Any]:
        """Fuzzy match institute metadata fields against extracted text."""
        text = extracted_text.lower()
        metadata_details = {}
        total_score = 0.0
        active_fields = 0

        for key, value in (metadata_fields or {}).items():
            if not value or not value.strip():
                continue

            active_fields += 1
            value_clean = value.strip().lower()
            score = fuzz.partial_ratio(value_clean, text)
            metadata_details[key] = score
            total_score += score

        overall_score = round(total_score / active_fields, 1) if active_fields > 0 else 0.0

        return {
            'metadata_score': overall_score,
            'metadata_details': metadata_details,
            'metadata_match': overall_score >= 70
        }

    def detect_duplicates_in_candidates(self, candidate_entries: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Detect potential duplicate certificates from candidate entries."""
        duplicates = []
        by_identifier = {}

        # Build certificate number index
        for c in candidate_entries:
            identifier = c.get('identifier_value')
            if identifier:
                normalized = identifier.lower().strip()
                by_identifier.setdefault(normalized, []).append(c)

        for normalized, entries in by_identifier.items():
            if len(entries) > 1:
                for e in entries:
                    duplicates.append({
                        'path': e['path'],
                        'duplicate_type': 'certificate_number',
                        'status': 'duplicate_confirmed',
                        'reason': f'same identifier {normalized}'
                    })

        # Pairwise similarity to find suspected duplicates
        n = len(candidate_entries)
        for i in range(n):
            for j in range(i + 1, n):
                a = candidate_entries[i]
                b = candidate_entries[j]

                # skip same path
                if a['path'] == b['path']:
                    continue

                text_similarity = fuzz.token_sort_ratio(a.get('extracted_text', ''), b.get('extracted_text', ''))
                base_a = os.path.splitext(a['filename'])[0].lower()
                base_b = os.path.splitext(b['filename'])[0].lower()

                name_pattern_match = (base_a == base_b) or fuzz.partial_ratio(base_a, base_b) > 90

                status = None
                reason = []
                if text_similarity >= 92 and name_pattern_match:
                    status = 'duplicate_confirmed'
                    reason.append('high text similarity and filename pattern')
                elif text_similarity >= 88:
                    status = 'duplicate_suspected'
                    reason.append('high text similarity')
                elif name_pattern_match and text_similarity >= 80:
                    status = 'duplicate_suspected'
                    reason.append('filename pattern and moderate text similarity')

                if status:
                    duplicates.append({
                        'path_a': a['path'],
                        'path_b': b['path'],
                        'duplicate_type': 'similarity',
                        'status': status,
                        'text_similarity': text_similarity,
                        'filename_similarity': fuzz.partial_ratio(base_a, base_b),
                        'reason': '; '.join(reason)
                    })

        return {
            'candidates_scanned': len(candidate_entries),
            'duplicates': duplicates
        }

    def _detect_certificate_type(self, pdf_path: str, extracted_text: str) -> Dict[str, Any]:
        """Helper to detect certificate type using filename + content matching."""
        filename = os.path.basename(pdf_path).lower()
        content = extracted_text.lower() if extracted_text else ''

        name_scores = {ct: fuzz.partial_ratio(ct.lower(), filename) for ct in self.certificate_types}
        text_scores = {ct: fuzz.partial_ratio(ct.lower(), content) for ct in self.certificate_types}

        combined_scores = {
            ct: round(0.45 * name_scores[ct] + 0.55 * text_scores[ct], 1)
            for ct in self.certificate_types
        }

        best_type = max(combined_scores, key=combined_scores.get)
        best_score = combined_scores[best_type]

        if best_score >= 75:
            status = 'Found and Valid'
        elif best_score >= 55:
            status = 'Found but Weak Match'
        else:
            status = 'Uncertain'

        return {
            'detected_type': best_type,
            'combined_score': best_score,
            'name_score': name_scores[best_type],
            'text_score': text_scores[best_type],
            'detection_status': status
        }

    def process_certificate(self, pdf_path: str, metadata_words: Any = None, metadata_fields: Dict[str, str] = None, format_pdf_path: str = None, interactive: bool = False, regulatory: str = None) -> Dict[str, Any]:
        """
        Comprehensive certificate verification
        
        :param pdf_path: Path to PDF certificate
        :param metadata_words: Expected metadata as list or dict (legacy list supported)
        :param metadata_fields: Expected metadata fields (institute_name, branch_name, city)
        :param format_pdf_path: Path to the format PDF
        :param regulatory: Optional regulatory format to validate (e.g., 'AICTE' or 'NAAC')
        :return: Verification results dictionary
        """
        try:
            # Extract text
            extracted_text = self.extract_text_from_pdf(pdf_path)

            if not extracted_text:
                logger.error("No text could be extracted from the PDF")
                return {
                    'text_extraction': False,
                    'certificate_type': None,
                    'detection_status': 'Missing',
                    'format_match': False,
                    'format_details': None
                }

            type_detection = self._detect_certificate_type(pdf_path, extracted_text)
            detected_type = type_detection['detected_type']

            # If text-based heuristics are required and confidence is low, apply fallback
            if type_detection['combined_score'] < 55 and not interactive:
                lowered = extracted_text.lower()
                if 'university' in lowered or 'degree' in lowered or 'graduation' in lowered:
                    detected_type = 'Academic Certificate'
                elif 'bank' in lowered or 'manager' in lowered:
                    detected_type = 'Certificate of Bank Manager'
                elif 'architect' in lowered:
                    detected_type = 'Certificate of Architecture'
                elif 'bar council' in lowered or 'advocate' in lowered:
                    detected_type = 'Certificate of Advocate'
                else:
                    detected_type = 'Professional Certificate'

            if not detected_type and interactive:
                print("\nCould not automatically detect certificate type.")
                print("Available certificate types:")
                for i, cert_type in enumerate(self.certificate_types, 1):
                    print(f"{i}. {cert_type}")

                while True:
                    try:
                        choice = int(input("\nEnter the number of the certificate type: "))
                        detected_type = self.certificate_types[choice - 1]
                        type_detection['detection_status'] = 'Found but Weak Match'
                        break
                    except (ValueError, IndexError):
                        print("Invalid selection. Please try again.")

            # Get user-defined format (from PDF or default/manual input)
            user_format = self.get_user_format(detected_type, format_pdf_path, interactive=interactive)

            # Compare certificate format
            format_analysis = self.compare_certificate_format(extracted_text, user_format)

            # Regulatory validation if requested (e.g., 'AICTE' or 'NAAC')
            regulatory_result = None
            if regulatory:
                regulatory_result = self.validate_regulatory_format(extracted_text, regulatory)

            cert_status = 'Found and Valid' if format_analysis['overall_similarity'] >= self.format_threshold else 'Found but Weak Match'

            # Resolve metadata configuration to support institute + branch + city checks
            metadata_fields = metadata_fields or {}
            if isinstance(metadata_words, dict):
                metadata_fields = {**metadata_fields, **metadata_words}
            elif isinstance(metadata_words, list):
                metadata_fields.setdefault('institute_name', ' '.join(metadata_words))

            metadata_result = self.match_institution_metadata(extracted_text, metadata_fields)

            date_fields = self.extract_dates_from_text(extracted_text)
            validity_status = self.determine_validity_status(detected_type, date_fields)
            authority_info = self.extract_authority_details(extracted_text, detected_type)
            identifier_info = self.extract_identifiers(extracted_text, detected_type)

            score_info = self.score_certificate_authenticity(
                text_extraction=bool(extracted_text),
                detection_status=type_detection['detection_status'],
                format_similarity=format_analysis['overall_similarity'],
                regulatory_check=regulatory_result,
                authority_match=authority_info['authority_match'],
                identifier_valid=identifier_info['identifier_valid'],
                metadata_match=metadata_result.get('metadata_match', False),
                validity_status=validity_status,
                duplicate_status=None
            )

            return {
                'text_extraction': bool(extracted_text),
                'certificate_type': detected_type,
                'detection_status': type_detection['detection_status'],
                'detection_score': type_detection['combined_score'],
                'format_match': format_analysis['overall_similarity'] >= self.format_threshold,
                'format_similarity': format_analysis['overall_similarity'],
                'certificate_status': cert_status,
                'format_details': format_analysis,
                'regulatory_check': regulatory_result,
                'date_fields': date_fields,
                'validity_status': validity_status,
                'authority_match': authority_info['authority_match'],
                'authority_details': authority_info['authority_details'],
                'metadata_score': metadata_result.get('metadata_score'),
                'metadata_match': metadata_result.get('metadata_match'),
                'metadata_details': metadata_result.get('metadata_details'),
                'identifier_found': identifier_info['identifier_found'],
                'identifier_value': identifier_info['identifier_value'],
                'identifier_valid': identifier_info['identifier_valid'],
                'identifiers': identifier_info['identifiers'],
                'authenticity_score': score_info['score'],
                'final_status': score_info['final_status'],
                'reasons': score_info['reasons'],
                'recommendations': score_info['recommendations']
            }

        except Exception as e:
            logger.error(f"Certificate processing error: {e}")
            return {
                'text_extraction': False,
                'certificate_type': None,
                'detection_status': 'Missing',
                'format_match': False,
                'format_details': None
            }

def main():
    import argparse

    parser = argparse.ArgumentParser(description='Certificate verification tool')
    parser.add_argument('pdf', nargs='?', help='Path to certificate PDF')
    parser.add_argument('--format-pdf', dest='format_pdf', help='Path to the format PDF', default=None)
    parser.add_argument('--regulatory', help="Regulatory format to validate (AICTE or NAAC)", default=None)
    parser.add_argument('--format-threshold', type=int, default=70, help='Overall format similarity threshold (0-100)')
    parser.add_argument('--regulatory-threshold', type=int, default=80, help='Per-element regulatory presence threshold (0-100)')
    parser.add_argument('--tesseract-path', help='Path to Tesseract executable', default=None)
    parser.add_argument('--validate-required-dir', help='Directory to scan and validate required project certificates', default=None)
    parser.add_argument('--interactive', action='store_true', help='Force interactive prompts')

    args = parser.parse_args()

    # Resolve inputs (CLI -> interactive fallback)
    if args.pdf:
        pdf_path = args.pdf
    elif args.interactive:
        pdf_path = input("Enter the path to the certificate PDF: ")
    else:
        # No PDF supplied; prompt (keeps backward compatibility)
        pdf_path = input("Enter the path to the certificate PDF: ")

    format_pdf_path = args.format_pdf if args.format_pdf else None
    regulatory = args.regulatory if args.regulatory else None

    # Validate threshold ranges
    if not (0 <= args.format_threshold <= 100):
        print("--format-threshold must be between 0 and 100")
        return
    if not (0 <= args.regulatory_threshold <= 100):
        print("--regulatory-threshold must be between 0 and 100")
        return

    # Metadata words for additional verification
    metadata_words = ["Pune", "Institute", "computer"]

    # Initialize verifier with user-supplied thresholds
    verifier = CertificateVerifier(
        tesseract_path=(args.tesseract_path or r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
        format_threshold=args.format_threshold,
        regulatory_threshold=args.regulatory_threshold
    )

    # If requested, validate the required certificates inside a directory
    if args.validate_required_dir:
        validate_dir = args.validate_required_dir
        if not os.path.isdir(validate_dir):
            print(f"Directory not found: {validate_dir}")
            return

        report = verifier.validate_required_certificates_in_dir(validate_dir)
        certificates_report = report.get('certificates', report)
        duplicates_report = report.get('duplicates', {})

        # Print a concise summary
        print("\n--- Required Certificates Validation Report ---")
        missing = []
        for req, info in certificates_report.items():
            status = 'FOUND' if info.get('matches') else 'MISSING'
            print(f"\n{req}: {status}")
            if info.get('matches'):
                for match in info['matches']:
                    val = match['validation']
                    print(f"  - {match['path']} (filename_score={match['filename_score']}, presence={val['overall_presence']:.1f}%, metadata_score={match.get('metadata_score',0):.1f}%)")
            else:
                missing.append(req)

        print(f"\nSummary: {len(certificates_report) - len(missing)} found, {len(missing)} missing")
        if missing:
            print("Missing certificates:")
            for m in missing:
                print(f"  - {m}")

        if duplicates_report and duplicates_report.get('duplicates'):
            print("\n--- Duplicate Certificate Analysis ---")
            for dup in duplicates_report['duplicates']:
                if 'path' in dup:
                    print(f"Confirmed: {dup['path']} ({dup['reason']})")
                else:
                    print(f"{dup['status']}: {dup['path_a']} <-> {dup['path_b']} (sim={dup['text_similarity']}%, reason={dup['reason']})")

        return

    # Process single certificate
    results = verifier.process_certificate(pdf_path, metadata_words, format_pdf_path, interactive=args.interactive, regulatory=regulatory)

    # Display results
    print("\n--- Certificate Verification Results ---")
    print(f"Certificate Type: {results['certificate_type'] or 'Not Detected'}")

    if results['format_details']:
        print("\nFormat Matching Details:")
        for element, similarity in results['format_details']['element_matches'].items():
            print(f"{element.capitalize()}: {similarity}% Match")

        print(f"\nOverall Format Similarity: {results['format_details']['overall_similarity']:.2f}%")
        print(f"Format Match: {'✅ Passed' if results['format_match'] else '❌ Failed'}")

        if results.get('regulatory_check'):
            rc = results['regulatory_check']
            print(f"\nRegulatory Check ({rc.get('regulatory')}): {'✅ Passed' if rc.get('pass') else '❌ Failed'}")
            for element, score in rc.get('element_matches', {}).items():
                print(f"{element.capitalize()}: {score}%")
            # Approval info for AICTE
            if rc.get('approval'):
                approval = rc['approval']
                print(f"Approval Value: {approval.get('value')}")
                print(f"Approval Valid: {'✅' if approval.get('valid') else '❌'}")
            print(f"Overall Presence: {rc.get('overall_presence'):.2f}%")

        print(f"\nText Extraction: {'✅ Passed' if results['text_extraction'] else '❌ Failed'}")

if __name__ == "__main__":
    main()
