import os
import sys
import traceback
from flask import Flask, request, jsonify, render_template, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
import json
from datetime import datetime, timedelta

app = Flask(__name__, template_folder='templates')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///hiscores.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
CORS(app)

class PlayerHiscores(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), nullable=False)
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    skills_data = db.Column(db.Text, nullable=False)

def parse_exp(exp_str):
    """
    Parse experience string, handling comma-separated and converting to integer
    Returns 0 if exp is empty or not parseable
    """
    try:
        # Remove commas and convert to integer
        return int(exp_str.replace(',', '').strip()) if exp_str and exp_str.strip() != '' else 0
    except (ValueError, AttributeError):
        return 0

def scrape_hiscores(username):
    """
    Scrape hiscores for a given username
    """
    # Replace spaces with underscores
    normalized_username = username.replace(' ', '_')
    
    url = f"https://2004.lostcity.rs/hiscores/player/{normalized_username}"
    
    try:
        # Fetch the hiscores page
        print(f"Attempting to scrape hiscores for {username}")
        response = requests.get(url, timeout=10)
        
        # Check if request was successful
        if response.status_code != 200:
            print(f"Failed to fetch hiscores. Status code: {response.status_code}")
            return {}
        
        # Parse the HTML
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Find the specific table with the attributes
        target_table = soup.find('table', attrs={
            'bgcolor': 'black', 
            'border': '0', 
            'cellpadding': '4'
        })
        
        if not target_table:
            print("Hiscores table not found in the page")
            return {}
        
        # Initialize skills dictionary
        skills_data = {}
        
        # Traverse table rows
        rows = target_table.find_all('tr')
        
        for row in rows:
            # Find all columns in the row
            cols = row.find_all('td')
            
            # Check if row has enough columns and contains a skill name
            if len(cols) >= 6:
                # Find skill name in the link
                skill_link = row.find('a', class_='c')
                
                if skill_link:
                    skill_name = skill_link.get_text(strip=True)
                    
                    # Parse rank, level, and exp from the columns
                    rank = parse_exp(cols[3].get_text(strip=True))
                    level = parse_exp(cols[4].get_text(strip=True))
                    exp = parse_exp(cols[5].get_text(strip=True))
                    
                    skills_data[skill_name] = {
                        'rank': rank,
                        'level': level,
                        'exp': exp
                    }
        
        print(f"Scraped skills for {username}: {list(skills_data.keys())}")
        return skills_data
    
    except requests.RequestException as e:
        print(f"Request error scraping hiscores for {username}: {e}")
        return {}
    except Exception as e:
        print(f"Unexpected error scraping hiscores for {username}: {e}")
        traceback.print_exc()
        return {}

def find_base_record(username, time_period):
    """
    Find the earliest record before the current time within the specified period
    time_period can be 'day', 'week', 'month'
    """
    now = datetime.utcnow()
    
    # Determine the time threshold based on the period
    time_thresholds = {
        'day': now - timedelta(days=1),
        'week': now - timedelta(weeks=1),
        'month': now - timedelta(days=30)
    }
    
    # Find all records for this username within the time period
    base_records = PlayerHiscores.query.filter(
        PlayerHiscores.username == username,
        PlayerHiscores.timestamp < now,
        PlayerHiscores.timestamp >= time_thresholds.get(time_period, time_thresholds['day'])
    ).order_by(PlayerHiscores.timestamp.asc()).all()
    
    print(f"Found {len(base_records)} base records for {username}")
    return base_records[0] if base_records else None

def calculate_skill_changes(base_data, current_data):
    """
    Calculate changes between base and current skill datasets
    """
    if not base_data:
        print("No base data found for comparison")
        return {skill: {
            'rank_change': 0,
            'level_change': 0,
            'exp_change': 0
        } for skill in current_data}
    
    changes = {}
    for skill, current_skill_data in current_data.items():
        base_skill = base_data.get(skill, {
            'rank': 0,
            'level': 0,
            'exp': 0
        })
        
        changes[skill] = {
            'rank_change': base_skill['rank'] - current_skill_data['rank'],
            'level_change': current_skill_data['level'] - base_skill['level'],
            'exp_change': current_skill_data['exp'] - base_skill['exp']
        }
    
    return changes

@app.route('/track', methods=['POST'])
def track_username():
    # Normalize username by replacing spaces with underscores
    username = request.json.get('username', '').replace(' ', '_')
    time_period = request.json.get('time_period', 'day')  # Default to day
    
    if not username:
        return jsonify({"error": "Username is required"}), 400
    
    try:
        # Scrape current hiscores
        print(f"Tracking username: {username}")
        skills_data = scrape_hiscores(username)
        
        if not skills_data:
            return jsonify({"error": "Could not retrieve skills data"}), 404
        
        # Find the most recent record for this normalized username
        latest_record = PlayerHiscores.query.filter_by(username=username).order_by(PlayerHiscores.timestamp.desc()).first()
        
        # Find base record for comparison
        base_record = find_base_record(username, time_period)
        
        # Calculate skill changes
        base_skills = json.loads(base_record.skills_data) if base_record else None
        skill_changes = calculate_skill_changes(base_skills, skills_data)
        
        # Check if the new skills data is different from the latest record
        should_save_record = not (latest_record and 
                                  json.loads(latest_record.skills_data) == skills_data)
        
        # Only save to database if there are changes
        if should_save_record:
            new_record = PlayerHiscores(
                username=username, 
                skills_data=json.dumps(skills_data)
            )
            db.session.add(new_record)
            db.session.commit()
            print(f"Saved new record for {username}")
            record_to_return = new_record
        else:
            print(f"No changes for {username}. Skipping database write.")
            record_to_return = latest_record
        
        return jsonify({
            "username": username,
            "timestamp": record_to_return.timestamp.isoformat(),
            "skills": skills_data,
            "skill_changes": skill_changes,
            "base_timestamp": base_record.timestamp.isoformat() if base_record else None,
            "time_period": time_period
        })
    
    except Exception as e:
        print(f"Tracking error for {username}: {e}")
        traceback.print_exc()
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500

@app.route('/skills/<username>', methods=['GET'])
def get_player_skills(username):
    # Find the most recent record for this username
    record = PlayerHiscores.query.filter_by(username=username).order_by(PlayerHiscores.timestamp.desc()).first()
    
    if not record:
        return jsonify({"error": "No skills found for this username"}), 404
    
    # Parse and return the skills data
    skills_data = json.loads(record.skills_data)
    
    return jsonify({
        "username": username,
        "timestamp": record.timestamp.isoformat(),
        "skills": skills_data
    })


@app.route('/')
def index():
    return render_template('index.html')


# Main Execution
if __name__ == '__main__':
    # Create database tables
    with app.app_context():
        db.create_all()
    
    # Run the Flask app
    app.run(host='0.0.0.0', port=5000, debug=True)