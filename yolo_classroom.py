from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from tempfile import TemporaryDirectory
from ultralytics import YOLO
import os
import cv2
from PIL import Image
import numpy as np
import hashlib
import traceback
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image as RLImage
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
import shutil
import zipfile
from io import BytesIO
from tempfile import NamedTemporaryFile
from mongoengine import connect,Document,StringField,FileField
from institute.models import Images
import requests
import cloudinary
import cloudinary.uploader
from io import BytesIO
from pydantic import BaseModel

print("\n" + "="*80)
print("🔗 Connecting to MongoDB...")
print("="*80)
try:
    connect(
        db="a13",
        host="mongodb+srv://neolearn02_db_user:3phXJLGvCqwHxtWH@a13.drvtvwx.mongodb.net/?retryWrites=true&w=majority&tls=true&tlsAllowInvalidCertificates=true&appName=Cluster0"
    )
    print(" MongoDB Connected Successfully!")
    print("="*80 + "\n")
except Exception as e:
    print(f" MongoDB Connection Failed: {e}")
    print("="*80 + "\n")

cloudinary.config(
    cloud_name='dy2sdcfxy',  
    api_key='492576988599259',        
    api_secret='bFHhLUnSDPqFAztC0520NFWk94U'  
)

class deficiency_report(Document):
    file = FileField(required=True)
    college = StringField(required=True)
    branch = StringField(required=True)
    meta = {
        'collection': 'deficiency_report'
    }

app = FastAPI()

model = YOLO("yolov8l.pt") 


def get_cloudinary_image_as_binary(cloudinary_url):
    """Retrieves a Cloudinary image as binary data from a given URL."""
    try:
        response = requests.get(cloudinary_url)
        response.raise_for_status()  # Raise an exception for HTTP errors
        return BytesIO(response.content).getvalue()
    except Exception as e:
        print(f"Error retrieving image from Cloudinary: {e}")
        return None

def save_binary_to_temp_file(binary_data, prefix='image', suffix='.jpg'):
    """Save binary image data to a temporary file."""
    with NamedTemporaryFile(delete=False, prefix=prefix, suffix=suffix) as temp_file:
        temp_file.write(binary_data)
        return temp_file.name


def annotate_yolo_image(image_path, results, output_path=None, color=(0, 255, 0), thickness=2):
    """Draw bounding boxes and labels from YOLO results on an image and save annotated output."""
    try:
        img = cv2.imread(image_path)
        if img is None:
            return None

        for result in results:
            for box in result.boxes:
                x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                label = result.names[int(box.cls)]
                confidence = float(box.conf)

                cv2.rectangle(img, (x1, y1), (x2, y2), color, thickness)
                cv2.putText(
                    img,
                    f"{label} {confidence:.2f}",
                    (x1, max(y1 - 10, 0)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    color,
                    1,
                    cv2.LINE_AA,
                )

        if output_path is None:
            output_path = image_path.replace('.jpg', '_annotated.jpg')

        cv2.imwrite(output_path, img)
        return output_path
    except Exception as e:
        print(f"Annotation error: {e}")
        return None


def calculate_dynamic_thresholds(intake, divisions, batches):
    """
    Calculate dynamic thresholds based on intake, divisions, and batches.
    """
    try:
        # Convert to integers with fallbacks
        intake = int(intake) if intake else 60
        divisions = int(divisions) if divisions else 1
        batches = int(batches) if batches else 1

        # Classroom: Assume 30 students per bench/table setup
        # Each division needs benches for its share of students
        classroom_threshold = max(5, (intake // divisions) // 30)

        # Lab: Assume 20 students per monitor workstation
        # Each division needs monitors for its share of students
        lab_threshold = max(3, (intake // divisions) // 20)

        return classroom_threshold, lab_threshold
    except (ValueError, TypeError):
        # Fallback to reasonable defaults
        return 10, 5

def check_image_quality(binary_data, seen_hashes=None):
    """
    Check image quality: resolution, brightness, blur, duplicate.
    Returns (quality_status, should_process)
    """
    try:
        # Load image
        image = Image.open(BytesIO(binary_data))
        img_array = np.array(image)
        if img_array.ndim == 3:
            gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
        else:
            gray = img_array
        
        # Resolution check - RELAXED THRESHOLD
        height, width = gray.shape
        if width < 400 or height < 300:  # Changed from 640x480 to 400x300
            return "Low Resolution", False
        
        # Brightness check - RELAXED THRESHOLD
        avg_brightness = np.mean(gray)
        if avg_brightness < 30:  # Changed from 50 to 30
            return "Too Dark", False
        
        # Blur check - RELAXED THRESHOLD
        laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
        if laplacian_var < 50:  # Changed from 100 to 50
            return "Blurry Image", False
        
        # Duplicate check (optional)
        if seen_hashes is not None:
            img_hash = hashlib.sha256(binary_data).hexdigest()
            if img_hash in seen_hashes:
                return "Duplicate Image", False
            seen_hashes.add(img_hash)
        
        return "Valid Quality", True
    except Exception as e:
        return f"Quality Check Error: {str(e)}", False

def process_classroom_images(binary_images, threshold_class, confidence_threshold=0.3):
    """
    Process classroom images with improved validation, object counts, and annotation.
    Returns a list of dicts containing enhanced per-image data.
    """
    rendered_entries = []
    seen_hashes = set()

    classroom_indicators = ['chair', 'desk', 'blackboard', 'person', 'book', 'potted plant']

    for binary_image in binary_images:
        if binary_image is None:
            continue

        entry = {
            'source_image': None,
            'annotated_image': None,
            'quality_status': 'Unknown',
            'room_type': 'Unknown',
            'required_vs_actual': '',
            'object_summary': '',
            'object_counts': {},
            'avg_confidence': 0.0,
            'status': 'Error',
            'recommendation': 'No data',
            'compliance': 'Non-compliant',
        }

        quality_status, should_process = check_image_quality(binary_image, seen_hashes)
        entry['quality_status'] = quality_status

        if not should_process:
            entry.update({
                'source_image': 'poor_quality_image.jpg',
                'room_type': 'Unusable',
                'status': 'Invalid Room',
                'recommendation': 'Image quality is poor; cannot perform reliable detection.',
                'compliance': 'Non-compliant',
            })
            rendered_entries.append(entry)
            continue

        temp_image_path = save_binary_to_temp_file(binary_image)
        entry['source_image'] = os.path.basename(temp_image_path)

        try:
            results = model.predict(temp_image_path, conf=confidence_threshold)

            obj_count = {}
            total_confidence = 0
            detection_count = 0

            for result in results:
                for box in result.boxes:
                    confidence = float(box.conf)
                    if confidence >= confidence_threshold:
                        label = result.names[int(box.cls)]
                        obj_count[label] = obj_count.get(label, 0) + 1
                        total_confidence += confidence
                        detection_count += 1

            if 'dining table' in obj_count:
                obj_count['bench'] = obj_count.get('bench', 0) + obj_count['dining table']
                del obj_count['dining table']

            bench_count = obj_count.get('bench', 0)
            classroom_score = sum(1 for indicator in classroom_indicators if obj_count.get(indicator, 0) > 0)
            avg_confidence = total_confidence / max(detection_count, 1)

            entry['avg_confidence'] = round(avg_confidence, 3)
            entry['object_counts'] = obj_count
            entry['object_summary'] = ', '.join([f"{k}({v})" for k, v in obj_count.items()]) or 'None'

            if bench_count > 0 and classroom_score >= 1:
                entry['room_type'] = 'Classroom'
                if bench_count >= threshold_class:
                    entry['status'] = 'Valid Room'
                    entry['recommendation'] = '-' 
                    entry['compliance'] = 'Compliant'
                else:
                    missing = threshold_class - bench_count
                    entry['status'] = 'Insufficient Equipment'
                    entry['recommendation'] = f'Add {missing} more benches/tables.'
                    entry['compliance'] = 'Non-compliant'
            elif bench_count > 0 and classroom_score == 0:
                entry['room_type'] = 'Uncertain'
                entry['status'] = 'Uncertain Detection'
                entry['recommendation'] = 'Image may not be a classroom; verify manually.'
                entry['compliance'] = 'Non-compliant'
            elif classroom_score >= 2 and bench_count == 0:
                entry['room_type'] = 'Invalid'
                entry['status'] = 'Invalid Room'
                entry['recommendation'] = 'Not a classroom; no benches/tables found.'
                entry['compliance'] = 'Non-compliant'
            else:
                entry['room_type'] = 'Invalid'
                entry['status'] = 'Invalid Room'
                entry['recommendation'] = f'Invalid image: {entry["source_image"]}'
                entry['compliance'] = 'Non-compliant'

            if avg_confidence < 0.5 and detection_count > 0:
                entry['status'] += ' (Low confidence)'
                if entry['recommendation'] == '-':
                    entry['recommendation'] = 'Detection confidence is low; verify manually.'
                    entry['compliance'] = 'Non-compliant'

            entry['required_vs_actual'] = f"Required benches: {threshold_class}, Actual benches: {bench_count}"

            annotated_path = annotate_yolo_image(temp_image_path, results)
            entry['annotated_image'] = annotated_path if annotated_path else ''

        except Exception as e:
            print(f"Error processing classroom image {temp_image_path}: {e}")
            entry['status'] = 'Error processing image'
            entry['recommendation'] = f'Processing failed: {str(e)}'
            entry['compliance'] = 'Non-compliant'
        finally:
            try:
                os.unlink(temp_image_path)
            except:
                pass

        rendered_entries.append(entry)

    return rendered_entries

def process_lab_images(binary_images, threshold_lab, confidence_threshold=0.3):
    """
    Process lab images with improved validation, object counts, and annotation.
    Returns a list of dicts containing enhanced per-image data.
    """
    rendered_entries = []
    seen_hashes = set()

    lab_indicators = ['keyboard', 'mouse', 'chair', 'desk', 'book']

    for binary_image in binary_images:
        if binary_image is None:
            continue

        entry = {
            'source_image': None,
            'annotated_image': None,
            'quality_status': 'Unknown',
            'room_type': 'Unknown',
            'required_vs_actual': '',
            'object_summary': '',
            'object_counts': {},
            'avg_confidence': 0.0,
            'status': 'Error',
            'recommendation': 'No data',
            'compliance': 'Non-compliant',
        }

        quality_status, should_process = check_image_quality(binary_image, seen_hashes)
        entry['quality_status'] = quality_status

        if not should_process:
            entry.update({
                'source_image': 'poor_quality_image.jpg',
                'room_type': 'Unusable',
                'status': 'Invalid Room',
                'recommendation': 'Image quality is poor; cannot perform reliable detection.',
                'compliance': 'Non-compliant',
            })
            rendered_entries.append(entry)
            continue

        temp_image_path = save_binary_to_temp_file(binary_image)
        entry['source_image'] = os.path.basename(temp_image_path)

        try:
            results = model.predict(temp_image_path, conf=confidence_threshold)

            obj_count = {}
            total_confidence = 0
            detection_count = 0

            for result in results:
                for box in result.boxes:
                    confidence = float(box.conf)
                    if confidence >= confidence_threshold:
                        label = result.names[int(box.cls)]
                        obj_count[label] = obj_count.get(label, 0) + 1
                        total_confidence += confidence
                        detection_count += 1

            obj_count['monitor'] = (obj_count.get('monitor', 0) + obj_count.pop('tv', 0) + obj_count.pop('laptop', 0))

            monitor_count = obj_count.get('monitor', 0)
            lab_score = sum(1 for indicator in lab_indicators if obj_count.get(indicator, 0) > 0)
            avg_confidence = total_confidence / max(detection_count, 1)

            entry['avg_confidence'] = round(avg_confidence, 3)
            entry['object_counts'] = obj_count
            entry['object_summary'] = ', '.join([f"{k}({v})" for k, v in obj_count.items()]) or 'None'

            if monitor_count > 0 and lab_score >= 1:
                entry['room_type'] = 'Lab'
                if monitor_count >= threshold_lab:
                    entry['status'] = 'Valid Room'
                    entry['recommendation'] = '-'
                    entry['compliance'] = 'Compliant'
                else:
                    missing = threshold_lab - monitor_count
                    entry['status'] = 'Insufficient Equipment'
                    entry['recommendation'] = f'Add {missing} more monitors/workstations.'
                    entry['compliance'] = 'Non-compliant'
            elif monitor_count > 0 and lab_score == 0:
                entry['room_type'] = 'Uncertain'
                entry['status'] = 'Uncertain Detection'
                entry['recommendation'] = 'Image may not be a lab; verify manually.'
                entry['compliance'] = 'Non-compliant'
            elif lab_score >= 2 and monitor_count == 0:
                entry['room_type'] = 'Invalid'
                entry['status'] = 'Invalid Room'
                entry['recommendation'] = 'Not a lab; no monitors/computers found.'
                entry['compliance'] = 'Non-compliant'
            else:
                entry['room_type'] = 'Invalid'
                entry['status'] = 'Invalid Room'
                entry['recommendation'] = f'Invalid image: {entry["source_image"]}'
                entry['compliance'] = 'Non-compliant'

            if avg_confidence < 0.5 and detection_count > 0:
                entry['status'] += ' (Low confidence)'
                if entry['recommendation'] == '-':
                    entry['recommendation'] = 'Detection confidence is low; verify manually.'
                    entry['compliance'] = 'Non-compliant'

            entry['required_vs_actual'] = f"Required monitors: {threshold_lab}, Actual monitors: {monitor_count}"

            annotated_path = annotate_yolo_image(temp_image_path, results)
            entry['annotated_image'] = annotated_path if annotated_path else ''

        except Exception as e:
            print(f"Error processing lab image {temp_image_path}: {e}")
            entry['status'] = 'Error processing image'
            entry['recommendation'] = f'Processing failed: {str(e)}'
            entry['compliance'] = 'Non-compliant'
        finally:
            try:
                os.unlink(temp_image_path)
            except:
                pass

        rendered_entries.append(entry)

    return rendered_entries


def process_canteen_images(binary_images, confidence_threshold=0.3):
    """
    Process canteen images to verify hygiene and facilities.
    Detects dining tables, seating, food-related items, and kitchen equipment.
    """
    rendered_entries = []
    seen_hashes = set()
    
    canteen_indicators = ['table', 'chair', 'person', 'bottle', 'cup', 'dining table']
    
    for binary_image in binary_images:
        if binary_image is None:
            continue
        
        entry = {
            'source_image': None,
            'quality_status': 'Unknown',
            'facility_type': 'Canteen',
            'object_summary': '',
            'object_counts': {},
            'avg_confidence': 0.0,
            'status': 'Under Review',
            'recommendation': 'Manual verification recommended',
            'compliance': 'Pending',
        }
        
        quality_status, should_process = check_image_quality(binary_image, seen_hashes)
        entry['quality_status'] = quality_status
        
        if not should_process:
            entry.update({
                'source_image': 'poor_quality_image.jpg',
                'status': 'Poor Quality',
                'recommendation': 'Image quality is poor; please re-upload.',
                'compliance': 'Pending',
            })
            rendered_entries.append(entry)
            continue
        
        temp_image_path = save_binary_to_temp_file(binary_image)
        entry['source_image'] = os.path.basename(temp_image_path)
        
        try:
            results = model.predict(temp_image_path, conf=confidence_threshold)
            
            obj_count = {}
            total_confidence = 0
            detection_count = 0
            
            for result in results:
                for box in result.boxes:
                    confidence = float(box.conf)
                    if confidence >= confidence_threshold:
                        label = result.names[int(box.cls)]
                        obj_count[label] = obj_count.get(label, 0) + 1
                        total_confidence += confidence
                        detection_count += 1
            
            table_count = obj_count.get('table', 0) + obj_count.get('dining table', 0)
            chair_count = obj_count.get('chair', 0)
            person_count = obj_count.get('person', 0)
            avg_confidence = total_confidence / max(detection_count, 1)
            
            entry['avg_confidence'] = round(avg_confidence, 3)
            entry['object_counts'] = obj_count
            entry['object_summary'] = ', '.join([f"{k}({v})" for k, v in obj_count.items()]) or 'None'
            
            if table_count > 0 and chair_count > 0:
                entry['status'] = f'Adequate Seating ({chair_count} chairs, {table_count} tables)'
                entry['compliance'] = 'Compliant'
                entry['recommendation'] = 'Seating capacity appears adequate'
            elif table_count > 0 or chair_count > 0:
                entry['status'] = 'Partial Facilities'
                entry['compliance'] = 'Partial'
                entry['recommendation'] = 'Some seating/tables found, verify completeness'
            else:
                entry['status'] = 'No Canteen Facilities'
                entry['compliance'] = 'Non-compliant'
                entry['recommendation'] = 'No seating or tables detected'
        
        except Exception as e:
            print(f"Error processing canteen image: {e}")
            entry['status'] = 'Processing Error'
            entry['compliance'] = 'Pending'
        finally:
            try:
                os.unlink(temp_image_path)
            except:
                pass
        
        rendered_entries.append(entry)
    
    return rendered_entries


def process_pwd_images(binary_images, confidence_threshold=0.3):
    """
    Process PWD (Persons with Disabilities) facilities images.
    Detects accessibility features, ramps, elevators, and accessible bathrooms.
    """
    rendered_entries = []
    seen_hashes = set()
    
    pwd_indicators = ['door', 'ramp', 'handrail', 'elevator', 'sign', 'person']
    
    for binary_image in binary_images:
        if binary_image is None:
            continue
        
        entry = {
            'source_image': None,
            'quality_status': 'Unknown',
            'facility_type': 'PWD Facilities',
            'object_summary': '',
            'object_counts': {},
            'avg_confidence': 0.0,
            'status': 'Under Review',
            'recommendation': 'Verify accessibility compliance',
            'compliance': 'Pending',
        }
        
        quality_status, should_process = check_image_quality(binary_image, seen_hashes)
        entry['quality_status'] = quality_status
        
        if not should_process:
            entry.update({
                'source_image': 'poor_quality_image.jpg',
                'status': 'Poor Quality',
                'recommendation': 'Image quality inadequate for accessibility assessment',
                'compliance': 'Pending',
            })
            rendered_entries.append(entry)
            continue
        
        temp_image_path = save_binary_to_temp_file(binary_image)
        entry['source_image'] = os.path.basename(temp_image_path)
        
        try:
            results = model.predict(temp_image_path, conf=confidence_threshold)
            
            obj_count = {}
            total_confidence = 0
            detection_count = 0
            
            for result in results:
                for box in result.boxes:
                    confidence = float(box.conf)
                    if confidence >= confidence_threshold:
                        label = result.names[int(box.cls)]
                        obj_count[label] = obj_count.get(label, 0) + 1
                        total_confidence += confidence
                        detection_count += 1
            
            door_count = obj_count.get('door', 0)
            handrail_count = obj_count.get('handrail', 0)
            ramp_count = obj_count.get('ramp', 0)
            avg_confidence = total_confidence / max(detection_count, 1)
            
            entry['avg_confidence'] = round(avg_confidence, 3)
            entry['object_counts'] = obj_count
            entry['object_summary'] = ', '.join([f"{k}({v})" for k, v in obj_count.items()]) or 'None'
            
            accessibility_features = sum([door_count > 0, handrail_count > 0, ramp_count > 0])
            
            if accessibility_features >= 2:
                entry['status'] = 'Good Accessibility'
                entry['compliance'] = 'Compliant'
                entry['recommendation'] = 'Adequate accessibility features detected'
            elif accessibility_features == 1:
                entry['status'] = 'Partial Accessibility'
                entry['compliance'] = 'Partial'
                entry['recommendation'] = 'Some accessibility features present; verify completeness'
            else:
                entry['status'] = 'Insufficient Accessibility'
                entry['compliance'] = 'Non-compliant'
                entry['recommendation'] = 'No visible accessibility features; needs improvement'
        
        except Exception as e:
            print(f"Error processing PWD image: {e}")
            entry['status'] = 'Processing Error'
            entry['compliance'] = 'Pending'
        finally:
            try:
                os.unlink(temp_image_path)
            except:
                pass
        
        rendered_entries.append(entry)
    
    return rendered_entries


def process_parking_images(binary_images, confidence_threshold=0.3):
    """
    Process parking facility images.
    Detects parking spaces, vehicles, markings, and signage.
    """
    rendered_entries = []
    seen_hashes = set()
    
    parking_indicators = ['car', 'truck', 'line', 'sign', 'pole', 'person']
    
    for binary_image in binary_images:
        if binary_image is None:
            continue
        
        entry = {
            'source_image': None,
            'quality_status': 'Unknown',
            'facility_type': 'Parking',
            'object_summary': '',
            'object_counts': {},
            'avg_confidence': 0.0,
            'status': 'Under Review',
            'recommendation': 'Verify parking capacity',
            'compliance': 'Pending',
        }
        
        quality_status, should_process = check_image_quality(binary_image, seen_hashes)
        entry['quality_status'] = quality_status
        
        if not should_process:
            entry.update({
                'source_image': 'poor_quality_image.jpg',
                'status': 'Poor Quality',
                'recommendation': 'Image quality inadequate; re-upload needed',
                'compliance': 'Pending',
            })
            rendered_entries.append(entry)
            continue
        
        temp_image_path = save_binary_to_temp_file(binary_image)
        entry['source_image'] = os.path.basename(temp_image_path)
        
        try:
            results = model.predict(temp_image_path, conf=confidence_threshold)
            
            obj_count = {}
            total_confidence = 0
            detection_count = 0
            
            for result in results:
                for box in result.boxes:
                    confidence = float(box.conf)
                    if confidence >= confidence_threshold:
                        label = result.names[int(box.cls)]
                        obj_count[label] = obj_count.get(label, 0) + 1
                        total_confidence += confidence
                        detection_count += 1
            
            vehicle_count = obj_count.get('car', 0) + obj_count.get('truck', 0)
            sign_count = obj_count.get('sign', 0)
            avg_confidence = total_confidence / max(detection_count, 1)
            
            entry['avg_confidence'] = round(avg_confidence, 3)
            entry['object_counts'] = obj_count
            entry['object_summary'] = ', '.join([f"{k}({v})" for k, v in obj_count.items()]) or 'None'
            
            if vehicle_count > 0 and sign_count > 0:
                entry['status'] = f'Active Parking ({vehicle_count} vehicles, {sign_count} signs)'
                entry['compliance'] = 'Compliant'
                entry['recommendation'] = 'Parking facility with proper signage'
            elif vehicle_count > 0:
                entry['status'] = f'Limited Signage ({vehicle_count} vehicles)'
                entry['compliance'] = 'Partial'
                entry['recommendation'] = 'Add proper parking signage and markings'
            else:
                entry['status'] = 'Capacity Status: Under Review'
                entry['compliance'] = 'Pending'
                entry['recommendation'] = 'Manual verification of parking capacity needed'
        
        except Exception as e:
            print(f"Error processing parking image: {e}")
            entry['status'] = 'Processing Error'
            entry['compliance'] = 'Pending'
        finally:
            try:
                os.unlink(temp_image_path)
            except:
                pass
        
        rendered_entries.append(entry)
    
    return rendered_entries


def process_washroom_images(binary_images, confidence_threshold=0.3):
    """
    Process washroom/bathroom facility images.
    Detects sanitary fixtures, sinks, mirrors, and cleanliness indicators.
    """
    rendered_entries = []
    seen_hashes = set()
    
    washroom_indicators = ['sink', 'mirror', 'toilet', 'soap', 'towel', 'dispenser']
    
    for binary_image in binary_images:
        if binary_image is None:
            continue
        
        entry = {
            'source_image': None,
            'quality_status': 'Unknown',
            'facility_type': 'Washroom',
            'object_summary': '',
            'object_counts': {},
            'avg_confidence': 0.0,
            'status': 'Under Review',
            'recommendation': 'Verify cleanliness and functionality',
            'compliance': 'Pending',
        }
        
        quality_status, should_process = check_image_quality(binary_image, seen_hashes)
        entry['quality_status'] = quality_status
        
        if not should_process:
            entry.update({
                'source_image': 'poor_quality_image.jpg',
                'status': 'Poor Quality',
                'recommendation': 'Image clarity inadequate for assessment',
                'compliance': 'Pending',
            })
            rendered_entries.append(entry)
            continue
        
        temp_image_path = save_binary_to_temp_file(binary_image)
        entry['source_image'] = os.path.basename(temp_image_path)
        
        try:
            results = model.predict(temp_image_path, conf=confidence_threshold)
            
            obj_count = {}
            total_confidence = 0
            detection_count = 0
            
            for result in results:
                for box in result.boxes:
                    confidence = float(box.conf)
                    if confidence >= confidence_threshold:
                        label = result.names[int(box.cls)]
                        obj_count[label] = obj_count.get(label, 0) + 1
                        total_confidence += confidence
                        detection_count += 1
            
            sink_count = obj_count.get('sink', 0)
            mirror_count = obj_count.get('mirror', 0)
            soap_count = obj_count.get('soap', 0) + obj_count.get('dispenser', 0)
            avg_confidence = total_confidence / max(detection_count, 1)
            
            entry['avg_confidence'] = round(avg_confidence, 3)
            entry['object_counts'] = obj_count
            entry['object_summary'] = ', '.join([f"{k}({v})" for k, v in obj_count.items()]) or 'None'
            
            essential_count = sum([sink_count > 0, mirror_count > 0, soap_count > 0])
            
            if essential_count >= 2:
                entry['status'] = 'Well-Equipped Washroom'
                entry['compliance'] = 'Compliant'
                entry['recommendation'] = 'Adequate sanitary fixtures present'
            elif essential_count == 1:
                entry['status'] = 'Partially Equipped'
                entry['compliance'] = 'Partial'
                entry['recommendation'] = 'Missing some essential washroom fixtures'
            else:
                entry['status'] = 'Inadequate Equipment'
                entry['compliance'] = 'Non-compliant'
                entry['recommendation'] = 'Requires upgrade of washroom facilities'
        
        except Exception as e:
            print(f"Error processing washroom image: {e}")
            entry['status'] = 'Processing Error'
            entry['compliance'] = 'Pending'
        finally:
            try:
                os.unlink(temp_image_path)
            except:
                pass
        
        rendered_entries.append(entry)
    
    return rendered_entries


def generate_pdf(classroom_data, lab_data, output_file, college_name, branch, intake, no_div, no_batches, inspection_scores=None, canteen_data=None, pwd_data=None, parking_data=None, washroom_data=None):
    doc = SimpleDocTemplate(output_file, pagesize=letter)
    elements = []
    styles = getSampleStyleSheet()

    title = Paragraph('Equipment Status and Recommendations', styles['Title'])
    elements.append(title)

    details_text = Paragraph(
        f'College: {college_name} | Branch: {branch} | Intake: {intake} | Divisions: {no_div} | Batches: {no_batches}',
        styles['Normal']
    )
    elements.append(Spacer(1, 12))
    elements.append(details_text)

    all_entries = (classroom_data or []) + (lab_data or [])
    total_images = len(all_entries)
    compliant_images = sum(1 for x in all_entries if x.get('compliance') == 'Compliant')
    non_compliant = total_images - compliant_images
    overall_conf = (sum(x.get('avg_confidence', 0) for x in all_entries) / total_images) if total_images > 0 else 0

    elements.append(Spacer(1, 12))
    elements.append(Paragraph('Overall Inspection Summary', styles['Heading2']))

    summary_table = Table([
        ['Total images analyzed', total_images],
        ['Compliant images', compliant_images],
        ['Non-compliant images', non_compliant],
        ['Average confidence', f'{overall_conf:.3f}'],
    ], colWidths=[3 * inch, 3 * inch])
    summary_table.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
    ]))
    elements.append(summary_table)

    if inspection_scores:
        elements.append(Spacer(1, 12))
        elements.append(Paragraph('Image Inspection Scoring', styles['Heading2']))
        score_table_data = [
            ['Metric', 'Score'],
            ['Image Quality Score', f"{inspection_scores.get('image_quality_score', 0)}%"],
            ['Classroom Compliance Score', f"{inspection_scores.get('classroom_compliance_score', 0)}%"],
            ['Lab Compliance Score', f"{inspection_scores.get('lab_compliance_score', 0)}%"],
            ['Smart Classroom Score', f"{inspection_scores.get('smart_classroom_score', 0)}%"],
            ['Evidence Completeness Score', f"{inspection_scores.get('evidence_completeness_score', 0)}%"],
            ['Doc-Image Consistency Score', f"{inspection_scores.get('doc_image_consistency_score', 0)}%"],
            ['Final Overall Score', f"{inspection_scores.get('final_overall_score', 0)}%"],
            ['Final Status', inspection_scores.get('final_overall_status', 'Non-Compliant')],
        ]
        score_table = Table(score_table_data, colWidths=[3 * inch, 3 * inch])
        score_table.setStyle(TableStyle([
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
        ]))
        elements.append(score_table)

    def add_room_section(name, entries):
        elements.append(Spacer(1, 12))
        elements.append(Paragraph(f'Report for {name.upper()}', styles['Heading2']))
        if not entries:
            elements.append(Paragraph('No images available', styles['Normal']))
            return

        for idx, entry in enumerate(entries, 1):
            elements.append(Spacer(1, 8))
            elements.append(Paragraph(f"{name.title()} Image {idx}: {entry.get('source_image')}" , styles['Heading3']))
            if entry.get('annotated_image'):
                annotated_path = entry['annotated_image']
                if os.path.exists(annotated_path):
                    elements.append(RLImage(annotated_path, width=3*inch, height=3*inch))
                    elements.append(Spacer(1, 4))

            # Determine which fields to display based on facility type
            facility_type = entry.get('facility_type', entry.get('room_type', 'Unknown'))
            
            details = [
                ['Facility Type', facility_type],
                ['Quality', entry.get('quality_status', '-')],
                ['Objects', entry.get('object_summary', '-')],
                ['Counts', ', '.join([f'{k}:{v}' for k, v in (entry.get('object_counts') or {}).items()]) or '-'],
                ['Average Confidence', str(entry.get('avg_confidence', '-'))],
                ['Status', entry.get('status', '-')],
                ['Recommendation', entry.get('recommendation', '-')],
                ['Compliance', entry.get('compliance', '-')],
            ]
            
            # Add room-specific fields if available
            if entry.get('room_type'):
                details.insert(2, ['Room Type', entry.get('room_type', '-')])
            if entry.get('required_vs_actual'):
                details.insert(3, ['Required vs Actual', entry.get('required_vs_actual', '-')])
            
            details_table = Table(details, colWidths=[2.0*inch, 4.0*inch])
            details_table.setStyle(TableStyle([
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('BACKGROUND', (0, 0), (-1, 0), colors.whitesmoke),
            ]))
            elements.append(details_table)

    # Add all facility sections
    add_room_section('classroom', classroom_data)
    add_room_section('lab', lab_data)
    if canteen_data:
        add_room_section('canteen', canteen_data)
    if pwd_data:
        add_room_section('pwd facilities', pwd_data)
    if parking_data:
        add_room_section('parking', parking_data)
    if washroom_data:
        add_room_section('washroom', washroom_data)

    doc.build(elements)


def get_image_evidence_summary(college_name, branch='entc'):
    """Gather image evidence metadata and smart/room compliance summary."""
    cloudinary_urls_lab = []
    cloudinary_urls_class = []

    documents = Images.objects(college=college_name)
    if not documents:
        return {
            'classroom_image_count': 0,
            'lab_image_count': 0,
            'classroom_valid_count': 0,
            'lab_valid_count': 0,
            'smart_classroom_evidence': 0,
            'status': 'missing',
            'classroom_image_list': [],
            'lab_image_list': []
        }

    for doc in documents:
        for item in doc.classroom:
            if item.get('branch') == branch:
                url = item.get('url')
                if url:
                    if isinstance(url, list):
                        cloudinary_urls_class.extend(url)
                    else:
                        cloudinary_urls_class.append(url)

    for doc in documents:
        for item in doc.lab:
            if item.get('branch') == branch:
                url = item.get('url')
                if url:
                    if isinstance(url, list):
                        cloudinary_urls_lab.extend(url)
                    else:
                        cloudinary_urls_lab.append(url)

    # Convert URLs to binary, filtering out None values
    binary_class_urls = [data for data in [get_cloudinary_image_as_binary(url) for url in cloudinary_urls_class] if data is not None]
    binary_lab_urls = [data for data in [get_cloudinary_image_as_binary(url) for url in cloudinary_urls_lab] if data is not None]

    classroom_entries = process_classroom_images(binary_class_urls, threshold_class=1)
    lab_entries = process_lab_images(binary_lab_urls, threshold_lab=1)

    classroom_image_count = len(classroom_entries)
    lab_image_count = len(lab_entries)

    classroom_valid_count = sum(1 for e in classroom_entries if e.get('status', '').startswith('Valid Room'))
    lab_valid_count = sum(1 for e in lab_entries if e.get('status', '').startswith('Valid Room'))

    smart_count = 0
    for e in classroom_entries + lab_entries:
        counts = e.get('object_counts', {}) or {}
        if any(k in counts and counts[k] > 0 for k in ['monitor', 'laptop', 'tv', 'projector']):
            smart_count += 1

    if classroom_image_count == 0 and lab_image_count == 0:
        status = 'missing'
    elif classroom_valid_count + lab_valid_count == 0:
        status = 'partial'
    else:
        status = 'sufficient'

    return {
        'classroom_image_count': classroom_image_count,
        'lab_image_count': lab_image_count,
        'classroom_valid_count': classroom_valid_count,
        'lab_valid_count': lab_valid_count,
        'smart_classroom_evidence': smart_count,
        'status': status,
        'classroom_image_list': cloudinary_urls_class,
        'lab_image_list': cloudinary_urls_lab,
        'classroom_entries': classroom_entries,
        'lab_entries': lab_entries,
    }


def calculate_image_inspection_score(classroom_entries, lab_entries, image_evidence=None, document_image_crosscheck=None):
    """Calculate inspection scores based on image evidence and cross-validation."""
    try:
        total_images = max(1, len((classroom_entries or [])) + len((lab_entries or [])))

        valid_quality = 0
        for entry in (classroom_entries or []) + (lab_entries or []):
            if entry.get('quality_status') == 'Valid Quality':
                valid_quality += 1

        image_quality_score = round((valid_quality / total_images) * 100, 1)

        # Classroom compliance score from evidence status
        classroom_status = (image_evidence or {}).get('status', 'missing')
        if classroom_status == 'sufficient':
            class_score = 100
        elif classroom_status == 'partial':
            class_score = 65
        else:
            class_score = 20

        # Lab compliance score from evidence status
        lab_status = (image_evidence or {}).get('status', 'missing')
        if lab_status == 'sufficient':
            lab_score = 100
        elif lab_status == 'partial':
            lab_score = 65
        else:
            lab_score = 20

        # Smart classroom verification from cross-check status
        smart_status = (document_image_crosscheck or {}).get('smart_evidence_status', 'missing')
        if smart_status == 'sufficient':
            smart_score = 100
        elif smart_status == 'partial':
            smart_score = 60
        else:
            smart_score = 15

        # Evidence completeness score
        evidence_status = (image_evidence or {}).get('status', 'missing')
        if evidence_status == 'sufficient':
            completeness_score = 100
        elif evidence_status == 'partial':
            completeness_score = 60
        else:
            completeness_score = 15

        # Document-image consistency
        consistency_status = (document_image_crosscheck or {}).get('overall_cross_validation_status', 'Non-Compliant')
        if consistency_status == 'Compliant':
            consistency_score = 100
        elif consistency_status == 'Partial':
            consistency_score = 65
        else:
            consistency_score = 20

        # Weighted final score - ADJUSTED FOR LENIENT SCORING
        final_score = round(
            0.10 * image_quality_score +  # Reduced from 0.20 (was harsh on bad images)
            0.25 * class_score +           # Increased from 0.20 (classroom matters more)
            0.25 * lab_score +             # Increased from 0.20 (lab matters more)
            0.15 * smart_score +
            0.15 * completeness_score +
            0.10 * consistency_score,
            1
        )

        if final_score >= 70:  # Lowered from 85 to 70
            overall_status = 'Compliant'
        elif final_score >= 50:  # Lowered from 60 to 50
            overall_status = 'Partially Compliant'
        else:
            overall_status = 'Non-Compliant'

        return {
            'image_quality_score': image_quality_score,
            'classroom_compliance_score': class_score,
            'lab_compliance_score': lab_score,
            'smart_classroom_score': smart_score,
            'evidence_completeness_score': completeness_score,
            'doc_image_consistency_score': consistency_score,
            'final_overall_score': final_score,
            'final_overall_status': overall_status,
        }

    except Exception as e:
        print(f"Error calculating image inspection score: {e}")
        return {
            'image_quality_score': 0,
            'classroom_compliance_score': 0,
            'lab_compliance_score': 0,
            'smart_classroom_score': 0,
            'evidence_completeness_score': 0,
            'doc_image_consistency_score': 0,
            'final_overall_score': 0,
            'final_overall_status': 'Non-Compliant',
        }


class data(BaseModel):
    college_name: str
    branch: str

@app.post("/generate-report/")
async def generate_report(info : data):
    try:
        document = Images.objects(college=info.college_name)
        if not document:
            raise HTTPException(
                status_code=404, 
                detail=f"No image documents found for college: {info.college_name}"
            )
        
        for doc in document:  # Loop through all documents
            classroom_images = doc.classroom
            lab_images = doc.lab
            
            if not classroom_images or not lab_images:
                raise HTTPException(
                    status_code=400, 
                    detail=f"Either classroom or lab images are missing in document {doc.id}. Please upload both."
                )

        # Initialize variables with defaults
        branch_intake = None
        no_div = None
        no_batches = None
        cloudinary_urls_lab = []
        cloudinary_urls_class = []
        
        # Extract classroom images and metadata
        for doc in document:
            for item in doc.classroom:
                if item.get('branch') == "entc" or item.get('branch') == info.branch:
                    if branch_intake is None:  # Set only once
                        branch_intake = item.get('itbk', 60)  # Default intake
                        no_div = item.get('nod', 1)  # Default divisions
                        no_batches = item.get('nob', 1)  # Default batches
                    
                    print(f"Processing classroom item: {item.get('url')}")
                    url = item.get('url')
                    if url:  # Ensure URLs exist
                        if isinstance(url, list):  # If URLs is a list
                            cloudinary_urls_class.extend(url)
                        else:  # If a single URL
                            cloudinary_urls_class.append(url)

        # Extract lab images
        for doc in document:
            for item in doc.lab:
                if item.get('branch') == "entc" or item.get('branch') == info.branch:
                    print(f"Processing lab item: {item.get('url')}")
                    url = item.get('url')
                    if url:  # Ensure URLs exist
                        if isinstance(url, list):  # If URLs is a list
                            cloudinary_urls_lab.extend(url)
                        else:  # If a single URL
                            cloudinary_urls_lab.append(url)
        
        # Validate that we have extracted metadata
        if branch_intake is None:
            print("Warning: Could not extract metadata from images. Using defaults.")
            branch_intake = 60
            no_div = 1
            no_batches = 1
        
        print(f"Extracted classroom URLs: {len(cloudinary_urls_class)}")
        print(f"Extracted lab URLs: {len(cloudinary_urls_lab)}")
        print(f"Metadata - Intake: {branch_intake}, Divisions: {no_div}, Batches: {no_batches}")
        
        # Convert URLs to binary, filtering out None values
        binary_class_urls = []
        for url in cloudinary_urls_class:
            binary_url = get_cloudinary_image_as_binary(url)
            if binary_url is not None:
                binary_class_urls.append(binary_url)
            else:
                print(f"Warning: Failed to retrieve image from {url}")
        
        binary_lab_urls = []
        for url in cloudinary_urls_lab:
            binary_url = get_cloudinary_image_as_binary(url)
            if binary_url is not None:
                binary_lab_urls.append(binary_url)
            else:
                print(f"Warning: Failed to retrieve image from {url}")
        
        if not binary_class_urls or not binary_lab_urls:
            print(f"Error: Insufficient binary images - Classrooms: {len(binary_class_urls)}, Labs: {len(binary_lab_urls)}")
            raise HTTPException(
                status_code=400,
                detail=f"Failed to retrieve images. Classrooms: {len(binary_class_urls)}, Labs: {len(binary_lab_urls)}"
            )
                                    

        # Calculate dynamic thresholds based on intake, divisions, and batches
        classroom_threshold, lab_threshold = calculate_dynamic_thresholds(
            branch_intake, no_div, no_batches
        )
        
        print(f"Dynamic thresholds - Classrooms: {classroom_threshold} benches, Labs: {lab_threshold} monitors")

        classroom_data = process_classroom_images(binary_class_urls, classroom_threshold)
        lab_data = process_lab_images(binary_lab_urls, lab_threshold)

        image_evidence = {
            'classroom_entries': classroom_data,
            'lab_entries': lab_data,
            'classroom_image_count': len(classroom_data),
            'lab_image_count': len(lab_data),
            'classroom_valid_count': sum(1 for e in classroom_data if e.get('status', '').startswith('Valid Room')),
            'lab_valid_count': sum(1 for e in lab_data if e.get('status', '').startswith('Valid Room')),
            'smart_classroom_evidence': sum(1 for e in classroom_data + lab_data if any(k in e.get('object_counts', {}) and e.get('object_counts', {}).get(k, 0) > 0 for k in ['monitor', 'laptop', 'tv', 'projector'])),
            'status': 'sufficient' if any(e.get('compliance') == 'Compliant' for e in classroom_data + lab_data) else 'partial',
        }

        final_scores = calculate_image_inspection_score(classroom_data, lab_data, image_evidence=image_evidence)
        print(f"Final scores calculated: {final_scores}")

        # Create a PDF file for the report with enhanced details
        output_pdf = f"{info.college_name}_{info.branch}_report.pdf"
        print(f"Generating PDF: {output_pdf}")
        
        try:
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
            )
            print(f"PDF generated successfully at: {output_pdf}")
        except Exception as pdf_error:
            print(f"Error during PDF generation: {pdf_error}")
            raise HTTPException(status_code=500, detail=f"PDF generation failed: {str(pdf_error)}")

        # Read the generated PDF file
        try:
            with open(output_pdf, 'rb') as pdf_file:
                pdf_data = pdf_file.read()
            print(f"PDF file read successfully, size: {len(pdf_data)} bytes")
        except Exception as file_error:
            print(f"Error reading PDF file: {file_error}")
            raise HTTPException(status_code=500, detail=f"Failed to read PDF file: {str(file_error)}")

        # Create a DeficiencyReport instance and save it to MongoDB
        try:
            Deficiency_report = deficiency_report(
                file=pdf_data,  # Save the binary data of the PDF
                college=info.college_name,
                branch=info.branch
            )
            Deficiency_report.save()
            print(f"Deficiency report saved to MongoDB with ID: {Deficiency_report.id}")
        except Exception as mongo_error:
            print(f"Error saving to MongoDB: {mongo_error}")
            raise HTTPException(status_code=500, detail=f"Failed to save report to database: {str(mongo_error)}")
        
        # Clean up temporary PDF file
        try:
            if os.path.exists(output_pdf):
                os.remove(output_pdf)
                print(f"Cleaned up temporary file: {output_pdf}")
        except Exception as cleanup_error:
            print(f"Warning: Could not clean up temporary file: {cleanup_error}")
        
        # Return the PDF file to the user with scores
        return {
            "message": "Report generated and saved successfully",
            "file_id": str(Deficiency_report.id),  # MongoDB file ID
            "inspection_scores": final_scores,
            "classroom_count": len(classroom_data),
            "lab_count": len(lab_data),
        }

    except HTTPException as he:
        # Re-raise HTTP exceptions with original status codes
        print(f"HTTP Exception: {he.detail}")
        raise he
    except Exception as e:
        print(f"Unexpected error in generate_report: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error generating report: {str(e)}")