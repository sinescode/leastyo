#!/usr/bin/env python3
import asyncio
import aiohttp
import json
import sys
import os
import random
import threading
import time
from typing import Dict, List, Tuple
from flask import Flask, render_template, request, jsonify, make_response

# ==============================
# Instagram Username Checker Web App
# ==============================

app = Flask(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/115.0 Safari/537.36",
    "x-ig-app-id": "936619743392459",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.instagram.com/",
    "Origin": "https://www.instagram.com",
    "Sec-Fetch-Site": "same-origin",
}

# Configuration
MAX_RETRIES = 10  # Maximum number of retries for a single username
INITIAL_DELAY = 1  # Initial delay in seconds
MAX_DELAY = 60  # Maximum delay in seconds
CONCURRENT_LIMIT = 5  # Number of concurrent requests

# Global variables to track progress
active_sessions = {}
session_results = {}

async def check_username(session: aiohttp.ClientSession, username: str, semaphore: asyncio.Semaphore, session_id: str) -> Tuple[str, str]:
    """
    Return (status, result_message)
    status = "ACTIVE", "AVAILABLE", or "ERROR"
    """
    url = f"https://i.instagram.com/api/v1/users/web_profile_info/?username={username}"
    retry_count = 0
    delay = INITIAL_DELAY

    while retry_count < MAX_RETRIES:
        # Check if session is still active
        if session_id not in active_sessions:
            return "CANCELLED", f"Cancelled: {username}"
            
        async with semaphore:
            try:
                async with session.get(url) as response:
                    if response.status == 404:
                        result = f"[AVAILABLE] {username}"
                        # Store result in session
                        if session_id in session_results:
                            session_results[session_id]['results'].append({
                                'username': username,
                                'status': 'AVAILABLE',
                                'message': result
                            })
                        return "AVAILABLE", result
                    elif response.status == 200:
                        data = await response.json()
                        if data.get("data", {}).get("user"):
                            result = f"[ACTIVE] {username}"
                            # Store result in session
                            if session_id in session_results:
                                session_results[session_id]['results'].append({
                                    'username': username,
                                    'status': 'ACTIVE',
                                    'message': result
                                })
                            return "ACTIVE", result
                        else:
                            result = f"[AVAILABLE] {username}"
                            # Store result in session
                            if session_id in session_results:
                                session_results[session_id]['results'].append({
                                    'username': username,
                                    'status': 'AVAILABLE',
                                    'message': result
                                })
                            return "AVAILABLE", result
                    else:
                        # Exponential backoff with jitter
                        delay = min(MAX_DELAY, delay * 2 + random.random())
                        retry_count += 1
                        status_msg = f"[RETRY {retry_count}/{MAX_RETRIES}] {username} - Status: {response.status}, Waiting: {delay:.2f}s"
                        # Store status in session
                        if session_id in session_results:
                            session_results[session_id]['status_updates'].append({
                                'username': username,
                                'message': status_msg
                            })
                        await asyncio.sleep(delay)
            except Exception as e:
                # Exponential backoff with jitter
                delay = min(MAX_DELAY, delay * 2 + random.random())
                retry_count += 1
                status_msg = f"[RETRY {retry_count}/{MAX_RETRIES}] {username} - Exception: {e}, Waiting: {delay:.2f}s"
                # Store status in session
                if session_id in session_results:
                    session_results[session_id]['status_updates'].append({
                        'username': username,
                        'message': status_msg
                    })
                await asyncio.sleep(delay)

    result = f"[ERROR] {username} - Max retries exceeded"
    # Store result in session
    if session_id in session_results:
        session_results[session_id]['results'].append({
            'username': username,
            'status': 'ERROR',
            'message': result
        })
    return "ERROR", result

async def process_usernames(usernames: List[str], account_data: Dict, session_id: str, filename: str):
    """Process a list of usernames asynchronously"""
    # Initialize session results
    session_results[session_id] = {
        'results': [],
        'status_updates': [],
        'completed': False,
        'filename': f"final_{os.path.splitext(filename)[0]}.json",
        'accounts': [],
        'stats': {
            'active_count': 0,
            'available_count': 0,
            'error_count': 0,
            'cancelled_count': 0,
            'total_count': len(usernames)
        }
    }
    
    # Create semaphore to limit concurrent requests
    semaphore = asyncio.Semaphore(CONCURRENT_LIMIT)
    
    # Create session with proper connection limits
    connector = aiohttp.TCPConnector(limit=CONCURRENT_LIMIT, limit_per_host=CONCURRENT_LIMIT)
    async with aiohttp.ClientSession(headers=HEADERS, connector=connector) as session:
        tasks = [check_username(session, u, semaphore, session_id) for u in usernames]
        results = await asyncio.gather(*tasks)

    # Process results
    final_results = [(u, status) for u, (status, _) in zip(usernames, results)]
    
    # Count and save only ACTIVE accounts
    saved_accounts = []  # List to store account objects
    saved_usernames = set()  # Track usernames to prevent duplicates
    
    active_count = 0
    available_count = 0
    error_count = 0
    cancelled_count = 0
    
    for username, status in final_results:
        if status == "ACTIVE" and username not in saved_usernames:
            saved_accounts.append(account_data[username])
            saved_usernames.add(username)
            active_count += 1
        elif status == "AVAILABLE":
            available_count += 1
        elif status == "ERROR":
            error_count += 1
        elif status == "CANCELLED":
            cancelled_count += 1
    
    # Update session results
    if session_id in session_results:
        session_results[session_id]['completed'] = True
        session_results[session_id]['accounts'] = saved_accounts
        session_results[session_id]['stats'] = {
            'active_count': active_count,
            'available_count': available_count,
            'error_count': error_count,
            'cancelled_count': cancelled_count,
            'total_count': len(usernames)
        }
    
    # Clean up session
    if session_id in active_sessions:
        del active_sessions[session_id]

def run_async_task(usernames, account_data, session_id, filename):
    """Run the async task in a new event loop"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(process_usernames(usernames, account_data, session_id, filename))
    loop.close()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/download/<session_id>')
def download_results(session_id):
    if session_id not in session_results or not session_results[session_id]['completed']:
        return jsonify({'error': 'Session not found or processing not completed'}), 404
    
    results = session_results[session_id]
    json_data = json.dumps(results['accounts'], indent=2, ensure_ascii=False)
    
    # Create response with JSON data
    response = make_response(json_data)
    response.headers['Content-Type'] = 'application/json'
    response.headers['Content-Disposition'] = f'attachment; filename={results["filename"]}'
    
    # Remove results from memory after download
    del session_results[session_id]
    
    return response

@app.route('/status/<session_id>')
def get_status(session_id):
    if session_id not in session_results:
        return jsonify({'error': 'Session not found'}), 404
    
    results = session_results[session_id]
    
    # Get new status updates and clear them
    status_updates = results.get('status_updates', [])
    if status_updates:
        results['status_updates'] = []
    
    # Get new results and clear them
    new_results = results.get('results', [])
    if new_results:
        results['results'] = []
    
    return jsonify({
        'completed': results['completed'],
        'status_updates': status_updates,
        'new_results': new_results,
        'stats': results['stats']
    })

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files and 'usernames' not in request.form:
        return jsonify({'error': 'No file or usernames provided'}), 400
    
    # Generate a unique session ID
    session_id = str(random.randint(100000, 999999))
    active_sessions[session_id] = True
    
    # Load usernames and account data
    usernames = []
    account_data = {}  # Map username to full account info
    
    # Check if file was uploaded
    if 'file' in request.files:
        file = request.files['file']
        if file and file.filename != '':
            if file.filename.endswith(".json"):
                try:
                    data = json.load(file)
                    for entry in data:
                        if entry.get("username"):
                            username = entry["username"]
                            usernames.append(username)
                            account_data[username] = entry
                except json.JSONDecodeError:
                    return jsonify({'error': 'Invalid JSON file'}), 400
            elif file.filename.endswith(".txt"):
                content = file.read().decode('utf-8')
                usernames = [line.strip() for line in content.splitlines() if line.strip()]
                # For txt files, create basic structure
                for username in usernames:
                    account_data[username] = {"username": username}
            else:
                return jsonify({'error': 'File must be a .json or .txt'}), 400
    
    # Check if usernames were provided in textarea
    if 'usernames' in request.form and request.form['usernames'].strip():
        text_usernames = [line.strip() for line in request.form['usernames'].splitlines() if line.strip()]
        usernames.extend(text_usernames)
        for username in text_usernames:
            account_data[username] = {"username": username}
    
    if not usernames:
        return jsonify({'error': 'No valid usernames found'}), 400
    
    # Start processing in a separate thread
    filename = "usernames.txt" if not request.files else file.filename
    thread = threading.Thread(
        target=run_async_task, 
        args=(usernames, account_data, session_id, filename)
    )
    thread.daemon = True
    thread.start()
    
    return jsonify({
        'message': f'Processing {len(usernames)} usernames',
        'session_id': session_id,
        'count': len(usernames)
    })

@app.route('/cancel/<session_id>', methods=['POST'])
def cancel_processing(session_id):
    if session_id in active_sessions:
        del active_sessions[session_id]
        return jsonify({'message': 'Processing cancelled'})
    else:
        return jsonify({'error': 'Session not found'}), 404

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)