import streamlit as st
import requests
import urllib.parse
import time
import re # Added for regex operations

# --- Constants ---
MY_STEAM_ID = "76561197989676140"  # The app owner's Steam ID
GAME_APP_ID = "2996990" # Fixed Game App ID

# --- Determine App Base URL ---
_raw_app_base_url = "http://localhost:8501" # Default for fallback
_determined_url_source = "default"
_warning_message_app_url = None

try:
    _candidate_url = st.context.url
    if _candidate_url and isinstance(_candidate_url, str) and _candidate_url.startswith("http"):
        _raw_app_base_url = _candidate_url
        _determined_url_source = "st.context.url"
    else:
        _warning_message_app_url = f"Could not determine a valid base URL from st.context.url (got: '{_candidate_url}'). Will use default."
except AttributeError:
    _warning_message_app_url = "st.context.url not available. This is expected for older Streamlit versions or some local setups. Will use default."

# Clean the determined URL to ensure it's a base URL without query strings or fragments
parsed_url = urllib.parse.urlparse(_raw_app_base_url)
APP_BASE_URL = urllib.parse.urlunparse(parsed_url._replace(query='', fragment=''))

if _warning_message_app_url:
    st.warning(f"{_warning_message_app_url} Source: {_determined_url_source}. Effective APP_BASE_URL for OpenID: {APP_BASE_URL}. OpenID redirect might fail if deployed and this is not the true public base URL.")


# --- Steam API Helper Functions ---

@st.cache_data(ttl=86400) # Cache for 1 hour
def get_steam_api_key():
    try:
        return st.secrets["STEAM_API_KEY"]
    except KeyError:
        # Intentionally no st.error here as it's a cached function
        # Errors related to API key absence are handled by functions calling this.
        return None

@st.cache_data(ttl=86400) # Cache for 1 day
def get_game_name(app_id):
    """Fetches game name from Steam API."""
    try:
        response = requests.get(f"https://store.steampowered.com/api/appdetails?appids={app_id}", timeout=10)
        response.raise_for_status()
        data = response.json()
        if data and str(app_id) in data and data[str(app_id)].get("success"):
            return data[str(app_id)]["data"].get("name", f"Game with App ID {app_id}")
    except requests.exceptions.RequestException: # Catching generic request exception
        pass # Errors are handled by returning a default name
    return f"Game with App ID {app_id}"

@st.cache_data(ttl=86400) # Cache for 1 hour
def get_steam_user_info(steam_id):
    """Fetches Steam user profile information (name, avatar)."""
    STEAM_API_KEY = get_steam_api_key()
    if not STEAM_API_KEY:
        return {"personaname": f"User {steam_id}", "avatarfull": "", "profileurl": f"https://steamcommunity.com/profiles/{steam_id}", "error": "API Key not configured"}
    
    url = f"https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/?key={STEAM_API_KEY}&steamids={steam_id}"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data.get("response", {}).get("players"):
            player = data["response"]["players"][0]
            return {
                "personaname": player.get("personaname", f"User {steam_id}"),
                "avatarfull": player.get("avatarfull", ""),
                "profileurl": player.get("profileurl", f"https://steamcommunity.com/profiles/{steam_id}")
            }
    except requests.exceptions.RequestException: # Catching generic request exception
        pass # Error handled by returning default dict with error message
    except Exception: # Catch any other unexpected errors during parsing etc.
        pass
    return {"personaname": f"User {steam_id}", "avatarfull": "", "profileurl": f"https://steamcommunity.com/profiles/{steam_id}", "error": "Profile fetch failed"}

@st.cache_data(ttl=60) # Cache inventory for 1 minute (was 10 mins, 1 min is safer for testing/rapid changes)
def fetch_steam_inventory(steam_id, app_id, context_id=2):
    """
    Fetches and processes Steam inventory for a user.
    Returns a dictionary: {market_hash_name: {'quantity': Q, 'icon_url': URL, 'name': Name, 'classid': ClassID}}
    or a string indicating an error/status.
    """
    detailed_items_data = {}
    asset_quantities = {} # classid: quantity
    all_descriptions = {} # classid: description_object
    start_assetid = None
    more_items = True
    page_count = 0

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept-Language': 'en-US,en;q=0.9'
    }

    while more_items:
        page_count += 1
        url = f"https://steamcommunity.com/inventory/{steam_id}/{app_id}/{context_id}?l=english&count=5000"
        if start_assetid:
            url += f"&start_assetid={start_assetid}"
        
        try:
            response = requests.get(url, headers=headers, timeout=20)
            response.raise_for_status()
            data = response.json()

            if data is None or not isinstance(data, dict):
                return "inventory_private_or_empty"

            if 'assets' in data and 'descriptions' in data:
                current_page_assets = data['assets']
                for asset in current_page_assets:
                    classid = asset.get('classid')
                    quantity = int(asset.get('amount', 1))
                    asset_quantities[classid] = asset_quantities.get(classid, 0) + quantity
                
                for desc in data['descriptions']:
                    classid = desc.get('classid')
                    if classid not in all_descriptions:
                        all_descriptions[classid] = desc
                
                if data.get('more_items') and data.get('last_assetid'):
                    start_assetid = data['last_assetid']
                    time.sleep(0.6) # Be respectful to the API
                else:
                    more_items = False
            
            elif data.get("total_inventory_count", 0) == 0 and not data.get('assets'):
                return "inventory_empty"
            else: # No assets on first page likely means private or other issue
                if page_count == 1:
                    return "inventory_private_or_error"
                more_items = False # Stop if subsequent pages are weirdly empty

        except requests.exceptions.Timeout:
            return "request_timeout"
        except requests.exceptions.RequestException:
            return "network_error"
        except ValueError: # JSONDecodeError
            return "api_decode_error"

    for classid, quantity in asset_quantities.items():
        if classid in all_descriptions:
            desc_obj = all_descriptions[classid]
            market_hash_name = desc_obj.get('market_hash_name', desc_obj.get('name', f'Unknown Item {classid}'))
            icon_url_suffix = desc_obj.get('icon_url', '')
            if icon_url_suffix:
                full_icon_url = f"https://community.cloudflare.steamstatic.com/economy/image/{icon_url_suffix.lstrip('/')}/120x50"
            else:
                full_icon_url = ""
            
            if market_hash_name not in detailed_items_data:
                detailed_items_data[market_hash_name] = {
                    'quantity': 0,
                    'icon_url': full_icon_url,
                    'name': desc_obj.get('name', 'Unknown Item'),
                    'classid': classid,
                    'tradable': desc_obj.get('tradable', 0) == 1,
                    'marketable': desc_obj.get('marketable', 0) == 1,
                    'tags': desc_obj.get('tags', [])
                }
            detailed_items_data[market_hash_name]['quantity'] += quantity
        else:
            market_hash_name = f"Unknown Item (ClassID: {classid})" # Should be rare
            detailed_items_data[market_hash_name] = {
                'quantity': quantity, 'icon_url': '', 'name': market_hash_name,
                'classid': classid, 'tradable': False, 'marketable': False, 'tags': []
            }
    return detailed_items_data

def analyze_inventories_for_streamlit(fixed_user_inv, your_inv):
    """Analyzes two inventories and returns structured results for display."""
    results = {
        'fixed_user_tradable_duplicates': {}, # Fixed User's items with quantity > 1
        'fixed_user_has_you_dont_dupes': {},  # Fixed User has Q>1, You don't have item
        'you_have_fixed_user_doesnt_dupes': {}   # You have Q>1, Fixed User doesn't have item
    }

    # Fixed User's tradable items (all duplicates)
    for item_hash, data in fixed_user_inv.items():
        if data['quantity'] > 1 and data['tradable']:
            results['fixed_user_tradable_duplicates'][item_hash] = data

    # Items Fixed User has (Q>1, tradable), and You don't have
    for item_hash, data in fixed_user_inv.items():
        if data['quantity'] > 1 and data['tradable'] and item_hash not in your_inv:
            results['fixed_user_has_you_dont_dupes'][item_hash] = data
            
    # Items You have (Q>1, tradable), and Fixed User doesn't have
    for item_hash, data in your_inv.items():
        if data['quantity'] > 1 and data['tradable'] and item_hash not in fixed_user_inv:
            results['you_have_fixed_user_doesnt_dupes'][item_hash] = data
            
    return results

# --- Function to Display Item Grid ---
def display_item_grid(items_dict, num_columns=5):
    if not items_dict:
        st.write("No items to display in this category.")
        return

    sorted_items = sorted(items_dict.items(), key=lambda x: x[1]['name']) # Sort by name
    
    # Calculate number of rows needed
    num_items = len(sorted_items)
    num_rows = (num_items + num_columns - 1) // num_columns

    for i in range(num_rows):
        cols = st.columns(num_columns)
        for j in range(num_columns):
            item_index = i * num_columns + j
            if item_index < num_items:
                item_hash, data = sorted_items[item_index]
                with cols[j]:
                    st.markdown(f"""
                    <div class="item-card">
                        <img src="{data['icon_url']}" alt="{data['name']}" title="{data['name']} (Tradable: {'Yes' if data['tradable'] else 'No'})">
                        <div class="item-name" title="{data['name']}">{data['name']}</div>
                        <div>Qty: {data['quantity']}</div>
                    </div>
                    """, unsafe_allow_html=True)

# --- Main Analysis Function ---
def run_inventory_analysis(trade_partner_steam_id, current_fixed_user_info, current_game_app_id, current_my_steam_id):
    """Fetches inventories, analyzes, and displays results."""

    your_user_info = get_steam_user_info(trade_partner_steam_id)
    if your_user_info.get("error") == "API Key not configured":
        st.error("The Steam Web API Key is not configured for this app. Your profile information cannot be fully loaded.")
    elif your_user_info.get("error"):
        st.warning(f"Could not load Your profile details: {your_user_info['error']}")
    
    profile_col1, profile_col2 = st.columns(2)
    inventory_owner = None
    inventory_you = None

    with profile_col1:
        st.subheader("Me")
        st.markdown(f'''
        <div class="user-profile">
            <img src="{current_fixed_user_info['avatarfull']}" alt="My Avatar">
            <div>
                <strong>{current_fixed_user_info['personaname']}</strong><br>
                <a href="{current_fixed_user_info['profileurl']}" target="_blank">View Steam Profile</a><br>
                <a href="https://steamcommunity.com/tradeoffer/new/?partner=29410412&token=saBTZD6_" target="_blank">Send Me a Trade Offer</a>
            </div>
        </div>
        ''', unsafe_allow_html=True)
        with st.spinner("Loading My inventory..."):
            inventory_owner_result = fetch_steam_inventory(current_my_steam_id, current_game_app_id)
        if isinstance(inventory_owner_result, str):
            st.error(f"Could not load My inventory: {inventory_owner_result.replace('_', ' ').capitalize()}.")
        elif not inventory_owner_result:
            st.info("My inventory is empty or could not be loaded.")
        else:
            inventory_owner = inventory_owner_result
            st.success("My inventory loaded.")

    with profile_col2:
        st.subheader(f"You")
        st.markdown(f"""
        <div class="user-profile">
            <img src="{your_user_info['avatarfull']}" alt="Your Avatar">
            <div>
                <strong>{your_user_info['personaname']}</strong><br>
                <a href="{your_user_info['profileurl']}" target="_blank">View Steam Profile</a>
            </div>
        </div>
        """, unsafe_allow_html=True)
        with st.spinner(f"Loading inventory for {trade_partner_steam_id}..."):
            inventory_you_result = fetch_steam_inventory(trade_partner_steam_id, current_game_app_id)
        if isinstance(inventory_you_result, str):
            st.error(f"Could not load Your inventory: {inventory_you_result.replace('_', ' ').capitalize()}.")
        elif not inventory_you_result:
            st.info(f"Your inventory is empty or could not be loaded.")
        else:
            inventory_you = inventory_you_result
            st.success(f"Your inventory loaded.")

    if inventory_owner and inventory_you: 
        analysis_results = analyze_inventories_for_streamlit(inventory_owner, inventory_you)
        
        st.markdown("---")
        st.header("Trade Opportunities")

        col1, col2 = st.columns(2)

        with col1:
            st.subheader("Items I have (duplicates, tradable) that YOU DON'T have:")
            display_item_grid(analysis_results['fixed_user_has_you_dont_dupes'])
        
        with col2:
            st.subheader("Items YOU have (duplicates, tradable) that I DON'T have:")
            display_item_grid(analysis_results['you_have_fixed_user_doesnt_dupes'])
        
        st.markdown("---")
        st.header("My Tradable Duplicates")
        st.write("All items in My inventory that I have more than one of and are tradable:")
        display_item_grid(analysis_results['fixed_user_tradable_duplicates'])
    
    elif not inventory_owner and not inventory_you: # Both failed or had issues
        st.info("Could not load inventories for analysis. Please check SteamIDs and profile privacy settings.")
    elif not inventory_owner: # Only owner failed
         st.error("Could not load My inventory. Analysis cannot proceed.")
    elif not inventory_you: # Only user failed
        st.error("Could not load Your inventory. Analysis cannot proceed.")


# --- Streamlit UI ---
st.set_page_config(page_title="Steam Inventory Trade Helper", layout="wide")

# Add custom CSS for item cards and user profiles
st.markdown("""
<style>
    .item-card {
        border: 1px solid #ddd;
        border-radius: 4px;
        padding: 10px;
        margin-bottom: 10px;
        text-align: center;
        height: 180px; /* Fixed height for consistency */
        display: flex;
        flex-direction: column;
        justify-content: space-between;
        align-items: center;
    }
    .item-card img {
        max-width: 100px; /* Adjust as needed */
        max-height: 50px; /* Adjust as needed */
        margin-bottom: 5px;
    }
    .item-name {
        font-size: 0.9em;
        word-wrap: break-word; /* Wraps long names */
        overflow-wrap: break-word; /* Ensures wrapping */
        white-space: normal; /* Allows wrapping */
        line-height: 1.2;
        max-height: 3.6em; /* Approx 3 lines */
        overflow: hidden;
        text-overflow: ellipsis;
    }
    .user-profile {
        display: flex;
        align-items: center;
        margin-bottom: 15px;
        padding: 10px;
        border: 1px solid #ddd;
        border-radius: 4px;
    }
    .user-profile img {
        width: 75px; /* Fixed avatar size */
        height: 75px;
        border-radius: 0; /* Make avatars square */
        margin-right: 15px;
    }
    .user-profile div {
        flex-grow: 1;
    }
</style>
""", unsafe_allow_html=True)

# Initialize session state variables if they don't exist (moved up)
if 'queried_steam_id' not in st.session_state:
    st.session_state.queried_steam_id = None
if 'analysis_triggered_once' not in st.session_state:
    st.session_state.analysis_triggered_once = False
if 'initiate_auto_analysis' not in st.session_state:
    st.session_state.initiate_auto_analysis = False
if 'last_analysis_timestamp' not in st.session_state:
    st.session_state.last_analysis_timestamp = 0
if 'last_analyzed_id' not in st.session_state:
    st.session_state.last_analyzed_id = None
if 'show_login_error' not in st.session_state:
    st.session_state.show_login_error = False

# --- Helper function to reset analysis state ---
def reset_analysis_state():
    """Resets session state variables to allow a new analysis."""
    st.session_state.queried_steam_id = None
    st.session_state.analysis_triggered_once = False
    st.session_state.initiate_auto_analysis = False
    st.query_params.clear()

# --- Handle Steam Login via OpenID ---
# Process OpenID callback parameters
raw_openid_mode = st.query_params.get("openid.mode")
current_openid_mode = None
if raw_openid_mode:
    current_openid_mode = raw_openid_mode[0] if isinstance(raw_openid_mode, list) else raw_openid_mode

if current_openid_mode:
    if current_openid_mode == 'id_res':
        raw_claimed_id = st.query_params.get("openid.claimed_id")
        
        actual_claimed_id_str = None
        if raw_claimed_id:
            actual_claimed_id_str = raw_claimed_id[0] if isinstance(raw_claimed_id, list) else raw_claimed_id
        
        if actual_claimed_id_str:
            match = re.search(r"https://steamcommunity.com/openid/id/(\d+)", actual_claimed_id_str)
            if match:
                steam_id_64 = match.group(1)
                st.session_state.queried_steam_id = steam_id_64
                st.session_state.initiate_auto_analysis = True # Trigger auto-analysis
                st.session_state.show_login_error = False # Reset error on successful attempt
            else: # Match failed
                st.session_state.queried_steam_id = None 
                st.session_state.initiate_auto_analysis = False 
                st.session_state.show_login_error = True
        else: # actual_claimed_id_str is None
            st.session_state.queried_steam_id = None
            st.session_state.initiate_auto_analysis = False
            st.session_state.show_login_error = True

        st.query_params.clear()

    elif current_openid_mode == 'cancel':
        st.session_state.show_login_error = True # Indicate cancellation as a form of login failure
        keys_to_clear = {k: None for k in st.query_params if k.startswith("openid.")}
        if keys_to_clear:
            st.query_params = keys_to_clear
            st.rerun()

# --- Fetch data needed for potential auto-analysis or main page display (moved up)
CURRENT_GAME_NAME = get_game_name(GAME_APP_ID)
fixed_user_info = get_steam_user_info(MY_STEAM_ID)
if fixed_user_info.get("error") == "API Key not configured":
    st.error("CRITICAL: The Steam Web API Key is not configured for this app. The application owner's data cannot be loaded, and most features will not work. Please configure `STEAM_API_KEY` in Streamlit secrets.")
elif fixed_user_info.get("error"):
    st.warning(f"Could not load application owner's profile details: {fixed_user_info['error']}")


# --- Auto-analysis Trigger (moved up, before main UI rendering) ---
if st.session_state.get('initiate_auto_analysis') and st.session_state.get('queried_steam_id'):
    st.session_state.initiate_auto_analysis = False # Consume the flag
    trade_partner_id_for_analysis = st.session_state.queried_steam_id
    
    # Validate SteamID format before proceeding
    if trade_partner_id_for_analysis and trade_partner_id_for_analysis.isdigit() and len(trade_partner_id_for_analysis) == 17:
        st.session_state.analysis_triggered_once = True
        st.session_state.last_analyzed_id = trade_partner_id_for_analysis
        st.session_state.last_analysis_timestamp = time.time()
    else:
        # This case might happen if OpenID parsing failed or manual input was bad
        st.session_state.queried_steam_id = None # Clear invalid ID
        st.session_state.analysis_triggered_once = False # Ensure input form shows again
        st.error(f"Invalid SteamID format received: '{trade_partner_id_for_analysis}'. Please provide a valid 64-bit SteamID.")

# --- Main Page UI ---

# Title for the application
st.markdown(f"""
<div style="text-align: center;">
    <h1>{CURRENT_GAME_NAME} Trade Helper</h1>
    <p>Analyze and compare Steam inventories for <strong>{CURRENT_GAME_NAME}</strong> to find trade opportunities.</p>
</div>
""", unsafe_allow_html=True)


if st.session_state.get('show_login_error'):
    st.error("Login via Steam failed or was cancelled. Please try again or enter your SteamID manually.")
    st.session_state.show_login_error = False # Consume the flag

# Display input options OR analysis results
if not st.session_state.get('analysis_triggered_once'):
    st.markdown("---")
    st.subheader("Choose Your Login Method")

    login_col1, login_col2 = st.columns([0.5, 0.5]) # Adjust column ratios if needed

    with login_col1:
        st.markdown("###### Option 1: Login with Steam")
        # Steam OpenID Login Button
        realm = APP_BASE_URL
        return_to = APP_BASE_URL
        
        params = {
            'openid.ns': 'http://specs.openid.net/auth/2.0',
            'openid.mode': 'checkid_setup',
            'openid.return_to': return_to,
            'openid.realm': realm,
            'openid.identity': 'http://specs.openid.net/auth/2.0/identifier_select',
            'openid.claimed_id': 'http://specs.openid.net/auth/2.0/identifier_select',
        }
        auth_url = f"https://steamcommunity.com/openid/login?{urllib.parse.urlencode(params)}"
        
        st.link_button("Login with Steam", auth_url, help="Log in using your Steam account to automatically fetch your SteamID.", use_container_width=True)
        
        # Tutorial for finding SteamID
        with st.expander("How to find your 64-bit SteamID"):
            st.markdown("""
            You can find your 64-bit SteamID (it's a 17-digit number) using your Steam Profile URL:

            *   Go to your Steam profile page.
            *   If you have a custom URL (e.g., `https://steamcommunity.com/id/yourcustomname/`), you can use a website like [SteamID.io](https://steamid.io/) or [SteamIDFinder.com](https://steamidfinder.com/). Paste your profile URL into their search box, and they will display your SteamID64.
            *   If your URL looks like `https://steamcommunity.com/profiles/7656119xxxxxxxxxx/`, the long number at the end **is** your SteamID64.
            """)
        
    with login_col2:
        st.markdown("###### Option 2: Enter SteamID Manually")
        manual_steam_id = st.text_input("Your 64-bit SteamID:", 
                                        value=st.session_state.get('queried_steam_id', ''), 
                                        placeholder="e.g., 76561197960287930",
                                        label_visibility="collapsed", # Hide label, already in markdown
                                        help="Enter your 17-digit SteamID64.")
        
        if st.button("Analyze Inventories", key="manual_analyze_button", use_container_width=True):
            if manual_steam_id:
                if manual_steam_id.isdigit() and len(manual_steam_id) == 17:
                    current_time = time.time()
                    COOLDOWN_SECONDS = 10
                    if manual_steam_id == st.session_state.get('last_analyzed_id') and \
                       (current_time - st.session_state.get('last_analysis_timestamp', 0)) < COOLDOWN_SECONDS:
                        st.warning(f"Please wait {COOLDOWN_SECONDS - int(current_time - st.session_state.get('last_analysis_timestamp', 0))} seconds before analyzing the same SteamID again.")
                    else:
                        st.session_state.queried_steam_id = manual_steam_id
                        st.session_state.analysis_triggered_once = True
                        st.session_state.last_analyzed_id = manual_steam_id
                        st.session_state.last_analysis_timestamp = current_time
                        st.rerun()
                else:
                    st.error("Invalid SteamID. Please enter a valid 17-digit 64-bit SteamID.")
            else:
                st.error("Please enter a SteamID or log in with Steam.")

else:
    # This block runs if analysis_triggered_once is True
    # (either from OpenID callback + auto-analysis, or manual input + button click)
    trade_partner_id_for_analysis = st.session_state.get('queried_steam_id')
    if trade_partner_id_for_analysis: # Ensure it's still set
        
        header_cols = st.columns([0.7, 0.3]) 
        with header_cols[0]:
            st.header("Analysis Results")
        with header_cols[1]:
            if st.button("Analyze Another SteamID", key="analyze_another_top", use_container_width=True):
                reset_analysis_state()
                st.rerun()

        # Run the main inventory analysis function
        run_inventory_analysis(trade_partner_id_for_analysis, fixed_user_info, GAME_APP_ID, MY_STEAM_ID)
    else:
        # Should not happen if logic is correct, but as a fallback:
        st.error("An error occurred. No SteamID found for analysis. Please try again.")
        st.session_state.analysis_triggered_once = False # Reset to show inputs
        st.rerun()

st.markdown("---")
st.caption("Steam Inventory Trade Helper | Not affiliated with Valve or Steam.")