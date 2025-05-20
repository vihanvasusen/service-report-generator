import os
import re
import datetime
import pdfplumber
import pandas as pd
import google.generativeai as genai
from flask import Flask, request, render_template, redirect, url_for, flash, send_file
from werkzeug.utils import secure_filename

# Initialize the Flask app
app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads/'
app.config['DOWNLOAD_FOLDER'] = 'downloads/'
app.config['FASSI_INDEX'] = 'fassi_index/'

for folder in [app.config['UPLOAD_FOLDER'], app.config['DOWNLOAD_FOLDER'], app.config['FASSI_INDEX']]:
    os.makedirs(folder, exist_ok=True)

# Configure Google Gemini API
API_KEY = "Your-API-Key"  # Your-API-KEY HERE
genai.configure(api_key=API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash') # Replace with latest model

def normalize_text(text):
    return re.sub(r'\W+', '', text.lower().strip())

def extract_dates_from_text(text):
    combined_prompt = f"{text}\n\nExtract Engineer arrival Dt/time, Activity End Dt/time"
    response = model.generate_content(combined_prompt)
    return response.text
    # print("Extracted Text from Gemini API:", extracted_text)  # Debugging output
    # return extracted_text

def store_in_fassi_index(file_name, extracted_text):
    normalized_file_name = normalize_text(file_name)
    fassi_file_path = os.path.join(app.config['FASSI_INDEX'], f"{normalized_file_name}.txt")

    if os.path.exists(fassi_file_path):
        with open(fassi_file_path, 'r') as file:
            stored_text = file.read()
        return stored_text
    else:
        with open(fassi_file_path, 'w') as file:
            file.write(extracted_text)
        return extracted_text

def extract_datetime_from_text(date_str):
    """Trying to extract datetime from a string, handling both 12-hour AM/PM and 24-hour formats."""
    try:
        datetime_formats = [
            "%d-%b-%Y, %I:%M %p",   
            "%d-%b-%Y, %H:%M",      
            "%d-%b-%Y, %H:%M %p",   
            "%Y-%m-%dT%H:%M:%S",    
            "%d/%m/%Y %H:%M",       
        ]

        for fmt in datetime_formats:
            try:
                # Trying to parse using each format
                return datetime.strptime(date_str.strip(), fmt)
            except ValueError:
                continue  # If this format fails, try the next one

        # If no format matches, fallback to the current datetime
        print(f"Unable to parse date: {date_str}. Using current datetime as fallback.")
        return datetime.now()
    
    except Exception as e:
        print(f"Error in date extraction: {e}")
        return datetime.now()  # Fallback to current datetime on error


def customer_appointment_details(logged_date, cust_scheduled_datetime, engineer_arrival_datetime, activity_end_datetime):
    if not engineer_arrival_datetime or not activity_end_datetime:
        print("Condition 4: Missing data for scheduled or activity end datetime.")
        return {"Logged Date": 1 if logged_date else 0, "Engineer Arrival": 1 if engineer_arrival_datetime else 0, "service provider stimulated Dt": "Data Missing"}

    if activity_end_datetime <= engineer_arrival_datetime:
        print("Condition 1: Activity end time is on or before scheduled time.")
        return {"Logged Date": 1, "Engineer Arrival": 1, "service provider stimulated Dt": 1}
    
    elif activity_end_datetime <= engineer_arrival_datetime + datetime.timedelta(minutes=20):   #(3, 4...30 mints)
        x = (activity_end_datetime - engineer_arrival_datetime).total_seconds()
        print(f"Condition 2: Delay within 20 minutes. Time difference is {x} seconds.")
        return {"Logged Date": 1, "Engineer Arrival": 1, "service provider stimulated Dt": 0}
    
    else:
        x = (activity_end_datetime - engineer_arrival_datetime).total_seconds()
        print(f"Condition 3: Delay morethan 20 minutes. Time difference is {x} seconds.")
        return {"Logged Date": 1, "Engineer Arrival": 1, "service provider stimulated Dt": 1}

@app.route('/download/<filename>')
def download_file(filename):
    file_path = os.path.join(app.config['DOWNLOAD_FOLDER'], filename)
    return send_file(file_path, as_attachment=True)

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        if 'file' not in request.files:
            flash('No file part')
            return redirect(request.url)

        uploaded_files = request.files.getlist('file')
        if not uploaded_files or uploaded_files[0].filename == '':
            flash('No selected file')
            return redirect(request.url)

        all_audit_summaries = []

        for uploaded_file in uploaded_files:
            filename = secure_filename(uploaded_file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            uploaded_file.save(filepath)

            with pdfplumber.open(filepath) as pdf:
                all_text = "".join(page.extract_text() or "" for page in pdf.pages)

            stored_text = store_in_fassi_index(filename, all_text)

            # Extracted text from Gemini API
            date_extraction_response = extract_dates_from_text(stored_text)
            print("Extracted Date from Gemini API:", date_extraction_response)

            cust_scheduled_datetime = None
            engineer_arrival_datetime = None
            activity_end_datetime = None

            # Clean and debug extracted text from Gemini API response
            cleaned_text = re.sub(r'\s+', ' ', date_extraction_response).strip()
            print("Cleaned Text:", cleaned_text)  # Check cleaned text structure

            # Enhanced regex pattern to capture both "Cust scheduled date and time" and "Activity End Dt/time"
            # scheduled_match = re.search(
            #     r'Cust\s*scheduled\s*date\s*and\s*time[:\s\*]*\**\s*(\d{2}-\w{3}-\d{4},\s*\d{2}:\d{2}\s*(?:AM|PM))',
            #     cleaned_text, re.IGNORECASE
            # )

            arrival_match = re.search(
                r'Engineer\s*arrival\s*(?:Date\s*and\s*Time|Dt/time)[:\s\*]*\**\s*(\d{2}-\w{3}-\d{4},\s*\d{2}:\d{2}\s*(?:AM|PM))',
                cleaned_text, re.IGNORECASE
            )
            end_match = re.search(
                r'Activity\s*End\s*Dt/time[:\s\*]*\**\s*(\d{2}-\w{3}-\d{4},\s*\d{2}:\d{2}\s*(?:AM|PM))',
                cleaned_text, re.IGNORECASE
            )

            # Check if dates were found and parse them
            if arrival_match:
                print(f"Engineer Arrival Match Found: {arrival_match.group(1)}")
                try:
                    engineer_arrival_datetime = datetime.datetime.strptime(arrival_match.group(1), "%d-%b-%Y, %I:%M %p")
                except ValueError as e:
                    print(f"Error parsing Engineer Arrival DateTime: {e}")
                    engineer_arrival_datetime = None
            else:
                print("Engineer Arrival Date Not Found")

            if end_match:
                print(f"Activity End Match Found: {end_match.group(1)}")
                try:
                    activity_end_datetime = datetime.datetime.strptime(end_match.group(1), "%d-%b-%Y, %I:%M %p")
                except ValueError as e:
                    print(f"Error parsing Activity End DateTime: {e}")
                    activity_end_datetime = None
            else:
                print("Activity End Date Not Found")

                        
        #     # generating audit summary based on key parameters
        #     audit_summary_prompt = (
        #     f"Summarize the following text focusing on the audit parameters: "
        #     f"Repair_notes, Closure match Repair Validation, Logged Date, "
        #     f"cust Scheduled, and service provider stimulated Dt.\n\n"
        #     f"Text:\n{stored_text}"
        # )
            # Define a detailed audit summary prompt with focused parameters
            audit_summary_prompt = (
                f"Generate an audit summary based on the following text. "
                f"The summary should extract and clearly specify the following audit parameters:\n\n"
                f"**Repair Notes**: Identify the section or text that provides any repair details or notes. "
                f"If available, include these notes verbatim.\n\n"
                f"**Closure Code and Repair Validation**: Check if a closure code is mentioned, "
                f"and determine whether it matches the repair notes provided. If a match or relevance "
                f"exists, specify it. Otherwise, indicate no match.\n\n"
                f"**Logged Date**: Look for any mention of the date on which the activity or repair was logged. "
                f"Extract this date, ensuring accuracy, and note any ambiguities if present.\n\n"
                f"**engineer_arrival_datetime**: Extract the engineer_arrival_datetime if mentioned. "
                f"Ensure that it is in a recognizable format (e.g., '07-Oct-2024, 11:00 AM').\n\n"
                f"**Service Provider Stimulated Date**: Look for the 'Service Provider Stimulated Date' or "
                f"similar phrasing that indicates when the service provider was expected to begin work. "
                f"Compare this date to the actual 'Activity End Date and Time' to assess promptness.\n\n"
                f"**Additional Findings**: Highlight any other relevant audit information, such as issues in scheduling, "
                f"delays, or lack of required information, if applicable.\n\n"
                f"Here is the text:\n\n{stored_text}"
            )       

            # Call Gemini API with prompt to generate a summary
            api_summary = model.generate_content(audit_summary_prompt).text
            api_summary_cleaned = api_summary.replace('*', '').strip()

            # Extract audit parameters
            audit_parameters = {
                "Repair_notes": re.search(r'Repair Notes\s*:\s*(.+)', stored_text, re.IGNORECASE).group(1) if re.search(r'Repair Notes\s*:\s*(.+)', stored_text, re.IGNORECASE) else None,
                "Closure_Code": re.search(r'Closure Code\s*:\s*(.*)', stored_text, re.IGNORECASE).group(1) if re.search(r'Closure Code\s*:\s*(.*)', stored_text, re.IGNORECASE) else None,
            }

            # Convert audit parameters to DataFrame
            df = pd.DataFrame([audit_parameters])

            # Define audit checks
            df['Repair_Audit'] = df['Repair_notes'].apply(lambda note: 1 if note else 0)
            df['Closure_Repair_Audit'] = df.apply(lambda row: 1 if (row['Closure_Code'] or '').lower() in (row['Repair_notes'] or '').lower() else 0, axis=1)

            # Run customer appointment audit
            appointment_audit = customer_appointment_details(
                logged_date=datetime.datetime.now(), 
                cust_scheduled_datetime=None,
                engineer_arrival_datetime=engineer_arrival_datetime,  
                activity_end_datetime=activity_end_datetime
            )

            # Compile audit summary into DataFrame
            audit_summary = pd.DataFrame({
                "Audit Parameter": [
                    "Repair_notes",
                    "Closure match Repair Validation",
                    "Logged Date",
                    "Engineer Arrival",
                    "service provider stimulated Dt"
                ],
                "Result (Yes=1/No=0)": [
                    df['Repair_Audit'][0],
                    df['Closure_Repair_Audit'][0],
                    appointment_audit["Logged Date"],
                    appointment_audit["Engineer Arrival"],
                    appointment_audit["service provider stimulated Dt"]
                ]
            })

            # Save audit summary as CSV
            csv_filename = f"{filename}_audit_summary.csv"
            csv_filepath = os.path.join(app.config['DOWNLOAD_FOLDER'], csv_filename)
            audit_summary.to_csv(csv_filepath, index=False)

            # Adding all audit summaries to the list, including the generated summary
            all_audit_summaries.append({
                "File": uploaded_file.filename,
                "Summary": audit_summary,
                "Generated_Summary": api_summary_cleaned,  
                "Download_Link": url_for('download_file', filename=csv_filename)
            })

        return render_template('summary.html', audit_summaries=all_audit_summaries)

    return render_template('index.html')
if __name__ == '__main__':
    app.run(debug=True)

