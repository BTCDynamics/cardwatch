import os
import json
import base64
import urllib.error
import urllib.request
from urllib.parse import quote
from datetime import date, datetime, timedelta
from uuid import uuid4

from flask import Flask, render_template, request, redirect, url_for, flash, session, send_from_directory
from sqlalchemy import inspect, text
from werkzeug.utils import secure_filename

from models import db, Card, CardImportStaging, ImageInbox

app = Flask(__name__)

app.secret_key = "cardwatch-dev-secret"

DATA_DIR = os.environ.get("CARDWATCH_DATA_DIR", os.path.join(app.root_path, "data"))
PERSISTENT_UPLOAD_FOLDER = os.path.join(DATA_DIR, "uploads")
os.makedirs(DATA_DIR, exist_ok=True)

app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{os.path.join(DATA_DIR, 'cardwatch.db')}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["UPLOAD_FOLDER"] = PERSISTENT_UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024  # 8 MB upload limit

ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

XIMILAR_API_TOKEN = os.environ.get("XIMILAR_API_TOKEN") or os.environ.get("XIMILAR_API_KEY")
XIMILAR_SPORT_CARD_ENDPOINT = os.environ.get(
    "XIMILAR_SPORT_CARD_ENDPOINT",
    "https://api.ximilar.com/collectibles/v2/sport_id"
)

db.init_app(app)


def ensure_upload_folder():
    """Create persistent upload storage.

    Images are saved directly to app.config["UPLOAD_FOLDER"].
    No filesystem linking, moving, or migration logic is used, so this works
    on Windows local development and Render persistent disks.
    """
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)


def ensure_database_columns():
    """Add newer columns to an existing SQLite database without wiping data."""
    inspector = inspect(db.engine)

    if "card" not in inspector.get_table_names():
        return

    existing_columns = {
        column["name"]
        for column in inspector.get_columns("card")
    }

    if "image_filename" not in existing_columns:
        db.session.execute(
            text("ALTER TABLE card ADD COLUMN image_filename VARCHAR(200)")
        )
        db.session.commit()

    if "estimated_value" not in existing_columns:
        db.session.execute(
            text("ALTER TABLE card ADD COLUMN estimated_value FLOAT")
        )
        db.session.commit()

    if "asking_price" not in existing_columns:
        db.session.execute(
            text("ALTER TABLE card ADD COLUMN asking_price FLOAT")
        )
        db.session.commit()

    if "sold_price" not in existing_columns:
        db.session.execute(
            text("ALTER TABLE card ADD COLUMN sold_price FLOAT")
        )
        db.session.commit()

    if "sold_date" not in existing_columns:
        db.session.execute(
            text("ALTER TABLE card ADD COLUMN sold_date VARCHAR(20)")
        )
        db.session.commit()

    if "sales_platform" not in existing_columns:
        db.session.execute(
            text("ALTER TABLE card ADD COLUMN sales_platform VARCHAR(100)")
        )
        db.session.commit()

    if "collection_type" not in existing_columns:
        db.session.execute(
            text("ALTER TABLE card ADD COLUMN collection_type VARCHAR(50) DEFAULT 'Inventory'")
        )
        db.session.commit()

    if "fulfillment_status" not in existing_columns:
        db.session.execute(
            text("ALTER TABLE card ADD COLUMN fulfillment_status VARCHAR(50) DEFAULT 'In Storage'")
        )
        db.session.commit()

    if "shipping_carrier" not in existing_columns:
        db.session.execute(
            text("ALTER TABLE card ADD COLUMN shipping_carrier VARCHAR(50)")
        )
        db.session.commit()

    if "tracking_number" not in existing_columns:
        db.session.execute(
            text("ALTER TABLE card ADD COLUMN tracking_number VARCHAR(100)")
        )
        db.session.commit()

    if "shipping_cost" not in existing_columns:
        db.session.execute(
            text("ALTER TABLE card ADD COLUMN shipping_cost FLOAT")
        )
        db.session.commit()

    if "shipped_date" not in existing_columns:
        db.session.execute(
            text("ALTER TABLE card ADD COLUMN shipped_date VARCHAR(20)")
        )
        db.session.commit()

    if "shipping_notes" not in existing_columns:
        db.session.execute(
            text("ALTER TABLE card ADD COLUMN shipping_notes TEXT")
        )
        db.session.commit()


    add_column_if_missing(
        "card",
        "acquisition_source",
        "ALTER TABLE card ADD COLUMN acquisition_source VARCHAR(50) DEFAULT 'Existing Inventory'"
    )
    add_column_if_missing(
        "card",
        "acquisition_date",
        "ALTER TABLE card ADD COLUMN acquisition_date VARCHAR(20)"
    )
    add_column_if_missing(
        "card",
        "acquisition_event",
        "ALTER TABLE card ADD COLUMN acquisition_event VARCHAR(150)"
    )

    add_column_if_missing(
        "card_import_staging",
        "acquisition_source",
        "ALTER TABLE card_import_staging ADD COLUMN acquisition_source VARCHAR(50) DEFAULT 'Existing Inventory'"
    )
    add_column_if_missing(
        "card_import_staging",
        "acquisition_date",
        "ALTER TABLE card_import_staging ADD COLUMN acquisition_date VARCHAR(20)"
    )
    add_column_if_missing(
        "card_import_staging",
        "acquisition_event",
        "ALTER TABLE card_import_staging ADD COLUMN acquisition_event VARCHAR(150)"
    )




def add_column_if_missing(table_name, column_name, ddl):
    """Safely add a SQLite column only if it does not already exist."""
    inspector = inspect(db.engine)

    if table_name not in inspector.get_table_names():
        return

    existing_columns = {
        column["name"]
        for column in inspector.get_columns(table_name)
    }

    if column_name not in existing_columns:
        db.session.execute(text(ddl))
        db.session.commit()

@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    """Serve uploaded card images from persistent disk."""
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


with app.app_context():
    db.create_all()
    ensure_database_columns()
    ensure_upload_folder()


def clean_value(value):
    if value:
        return value.strip()
    return None


ACQUISITION_SOURCE_OPTIONS = [
    "Existing Inventory",
    "Cash Purchase",
    "Trade-In",
    "Bulk Collection",
    "Pack Pull",
    "Personal Collection",
    "Other",
]

ACQUISITION_DASHBOARD_SOURCES = {
    "Cash Purchase",
    "Trade-In",
    "Bulk Collection",
    "Pack Pull",
    "Other",
}


def acquisition_value(value):
    value = clean_value(value)
    return value or "Existing Inventory"


def acquisition_date_value(form_data):
    """Return an acquisition date only for true acquisition sources.

    Existing Inventory means the card was already owned before it was entered
    into CardDesk, so it should not appear in dashboard acquisition counts.
    """
    source = acquisition_value(form_data.get("acquisition_source"))

    if source == "Existing Inventory":
        return None

    return form_data.get("acquisition_date") or None


def purchase_date_value(form_data):
    """Keep purchase_date for compatibility without treating entry date as acquisition date."""
    source = acquisition_value(form_data.get("acquisition_source"))

    if source == "Existing Inventory":
        return form_data.get("purchase_date") or None

    return form_data.get("purchase_date") or form_data.get("acquisition_date") or None


def is_dashboard_acquisition(card):
    return getattr(card, "acquisition_source", None) in ACQUISITION_DASHBOARD_SOURCES



def parse_card_date(value):
    """Convert saved card date strings into a date object for reliable range filtering."""
    if not value:
        return None

    value = str(value).strip()

    for date_format in ("%Y-%m-%d", "%m/%d/%Y", "%-m/%-d/%Y"):
        try:
            return datetime.strptime(value, date_format).date()
        except ValueError:
            continue

    # Windows does not support %-m / %-d, so try a manual fallback.
    try:
        month, day, year = value.split("/")
        return date(int(year), int(month), int(day))
    except (ValueError, TypeError):
        return None


def generate_card_code():
    last_card = Card.query.order_by(Card.id.desc()).first()

    if not last_card:
        return "CW-000001"

    next_number = last_card.id + 1

    return f"CW-{next_number:06d}"


def allowed_image(filename):
    if not filename or "." not in filename:
        return False

    extension = filename.rsplit(".", 1)[1].lower()

    return extension in ALLOWED_IMAGE_EXTENSIONS


def save_uploaded_image(file_storage):
    if not file_storage or not file_storage.filename:
        return None

    if not allowed_image(file_storage.filename):
        flash("Image must be a PNG, JPG, JPEG, GIF, or WEBP file.")
        return None

    original_filename = secure_filename(file_storage.filename)
    extension = original_filename.rsplit(".", 1)[1].lower()
    unique_filename = f"{uuid4().hex}.{extension}"
    save_path = os.path.join(app.config["UPLOAD_FOLDER"], unique_filename)

    file_storage.save(save_path)

    return unique_filename


def delete_image_file(filename):
    if not filename:
        return

    image_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)

    if os.path.exists(image_path):
        os.remove(image_path)


def save_uploaded_image_with_source(file_storage):
    """Save an uploaded image and also return the browser/client filename."""
    source_filename = secure_filename(file_storage.filename) if file_storage and file_storage.filename else None
    image_filename = save_uploaded_image(file_storage)
    return image_filename, source_filename


def mark_inbox_image_used(inbox_image, card_id):
    """Mark an Image Inbox item as attached to an inventory card."""
    if not inbox_image:
        return

    inbox_image.status = "Used"
    inbox_image.used_card_id = card_id
    inbox_image.used_at = datetime.utcnow()


def get_available_inbox_images(limit=24):
    """Return recent unattached images available for Add Card."""
    return (
        ImageInbox.query
        .filter(ImageInbox.status == "Available")
        .order_by(ImageInbox.created_at.desc(), ImageInbox.id.desc())
        .limit(limit)
        .all()
    )


def normalize_year(value):
    if value in (None, ""):
        return None

    text_value = str(value).strip()

    if not text_value:
        return None

    for token in text_value.replace("/", " ").replace("-", " ").split():
        if token.isdigit() and len(token) == 4:
            try:
                return int(token)
            except ValueError:
                return None

    if text_value.isdigit():
        try:
            return int(text_value)
        except ValueError:
            return None

    return None


def recursive_values_by_key(data, wanted_keys):
    """Return values whose key name loosely matches one of wanted_keys anywhere in nested JSON."""
    matches = []
    wanted = {key.lower().replace("_", "").replace(" ", "") for key in wanted_keys}

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                normalized_key = str(key).lower().replace("_", "").replace(" ", "")
                if normalized_key in wanted and value not in (None, "", [], {}):
                    matches.append(value)
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    return matches


def first_text_value(data, keys):
    for value in recursive_values_by_key(data, keys):
        if isinstance(value, dict):
            for nested_key in ("name", "value", "text", "label"):
                if value.get(nested_key):
                    return str(value.get(nested_key)).strip()
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    for nested_key in ("name", "value", "text", "label"):
                        if item.get(nested_key):
                            return str(item.get(nested_key)).strip()
                elif item not in (None, ""):
                    return str(item).strip()
        else:
            return str(value).strip()
    return None


def best_confidence(data):
    candidates = []
    for value in recursive_values_by_key(data, ["confidence", "probability", "score"]):
        try:
            number = float(value)
            if number <= 1:
                number *= 100
            candidates.append(number)
        except (TypeError, ValueError):
            continue
    return max(candidates) if candidates else None


def infer_card_type(grading_company, actual_grade, cert_number):
    if grading_company or actual_grade or cert_number:
        return "Graded"
    return "Raw"


def extract_card_data_from_ximilar(response_json):
    """Best-effort parser for Ximilar's nested response formats."""
    payload = response_json or {}

    player_name = first_text_value(payload, [
        "player", "player_name", "name", "subject", "person", "athlete"
    ])
    year = normalize_year(first_text_value(payload, ["year", "season", "date", "released"]))
    brand = first_text_value(payload, ["brand", "manufacturer", "company", "producer"])
    set_name = first_text_value(payload, ["set", "set_name", "series", "product", "subset"])
    card_number = first_text_value(payload, ["card_number", "card number", "number", "card no", "card_no"])
    variation = first_text_value(payload, ["variation", "parallel", "refractor", "insert", "features"])
    sport = first_text_value(payload, ["sport", "category", "league"]) or "Baseball"
    grading_company = first_text_value(payload, ["grading_company", "grader", "grading", "slab_company"])
    actual_grade = first_text_value(payload, ["actual_grade", "grade", "rating"])
    cert_number = first_text_value(payload, ["cert_number", "certificate", "cert", "serial", "certification_number"])

    # Avoid using the overall object name as player when it looks like a full card title.
    if player_name and any(piece in player_name.lower() for piece in ["topps", "panini", "upper deck", "fleer", "donruss"]):
        # Keep it as notes/context instead of forcing it into player.
        player_name = first_text_value(payload, ["player_name", "athlete", "subject", "person"]) or player_name

    return {
        "player_name": player_name,
        "year": year,
        "sport": sport,
        "brand": brand,
        "set_name": set_name,
        "card_number": card_number,
        "variation": variation,
        "grading_company": grading_company,
        "actual_grade": actual_grade,
        "cert_number": cert_number,
        "card_type": infer_card_type(grading_company, actual_grade, cert_number),
        "ai_confidence": best_confidence(payload),
    }


def call_ximilar_for_image(image_filename):
    """Send one saved image to Ximilar and return the raw JSON response."""
    if not XIMILAR_API_TOKEN:
        raise RuntimeError("Missing XIMILAR_API_TOKEN environment variable.")

    image_path = os.path.join(app.config["UPLOAD_FOLDER"], image_filename)

    with open(image_path, "rb") as image_file:
        encoded_image = base64.b64encode(image_file.read()).decode("utf-8")

    payload = {
        "records": [
            {
                "_base64": encoded_image
            }
        ]
    }

    request_data = json.dumps(payload).encode("utf-8")
    api_request = urllib.request.Request(
        XIMILAR_SPORT_CARD_ENDPOINT,
        data=request_data,
        headers={
            "Authorization": f"Token {XIMILAR_API_TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(api_request, timeout=45) as response:
            response_body = response.read().decode("utf-8")
            return json.loads(response_body)
    except urllib.error.HTTPError as error:
        error_body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Ximilar HTTP {error.code}: {error_body}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Ximilar connection error: {error.reason}") from error


def find_probable_duplicate_from_staging(staged_card):
    if not staged_card.player_name:
        return None

    query = Card.query.filter(Card.player_name.ilike(staged_card.player_name))

    if staged_card.sport:
        query = query.filter(Card.sport == staged_card.sport)
    if staged_card.year:
        query = query.filter(Card.year == staged_card.year)
    if staged_card.brand:
        query = query.filter(Card.brand.ilike(staged_card.brand))
    if staged_card.card_number:
        query = query.filter(Card.card_number.ilike(staged_card.card_number))

    if staged_card.variation:
        query = query.filter(Card.variation.ilike(staged_card.variation))
    else:
        query = query.filter(db.or_(Card.variation.is_(None), Card.variation == ""))

    return query.first()


def get_storage_locations():
    """Return all unique storage locations currently used by cards."""
    rows = (
        db.session.query(Card.storage_location)
        .filter(Card.storage_location.isnot(None))
        .filter(Card.storage_location != "")
        .distinct()
        .order_by(Card.storage_location.asc())
        .all()
    )

    return [row[0] for row in rows if row[0]]


def get_storage_summary():
    """Return storage locations with record count, total quantity, and value totals."""
    cards = (
        Card.query
        .filter(Card.status != "Sold")
        .filter(Card.storage_location.isnot(None))
        .filter(Card.storage_location != "")
        .order_by(Card.storage_location.asc(), Card.player_name.asc())
        .all()
    )

    summary = {}

    for card in cards:
        location = card.storage_location

        if location not in summary:
            summary[location] = {
                "location": location,
                "unique_cards": 0,
                "total_cards": 0,
                "purchase_cost": 0,
                "estimated_value": 0,
                "ready_to_sell": 0,
            }

        quantity = card.quantity or 1
        summary[location]["unique_cards"] += 1
        summary[location]["total_cards"] += quantity

        if card.purchase_price:
            summary[location]["purchase_cost"] += card.purchase_price * quantity

        if card.estimated_value:
            summary[location]["estimated_value"] += card.estimated_value * quantity

        if card.status == "Ready to Sell":
            summary[location]["ready_to_sell"] += quantity

    return list(summary.values())


def get_deal_cart_ids():
    """Return selected deal-cart card IDs stored in the user session."""
    return [int(card_id) for card_id in session.get("deal_cart", [])]


def save_deal_cart_ids(card_ids):
    """Store unique deal-cart card IDs in the session."""
    clean_ids = []

    for card_id in card_ids:
        try:
            clean_id = int(card_id)
        except (TypeError, ValueError):
            continue

        if clean_id not in clean_ids:
            clean_ids.append(clean_id)

    session["deal_cart"] = clean_ids
    session.modified = True


def get_deal_cart_cards():
    """Return active, unsold cards currently in the deal cart."""
    cart_ids = get_deal_cart_ids()

    if not cart_ids:
        return []

    cards = (
        Card.query
        .filter(Card.id.in_(cart_ids))
        .order_by(Card.player_name.asc())
        .all()
    )

    active_cards = [card for card in cards if card.status == "Active"]

    if len(active_cards) != len(cards):
        save_deal_cart_ids([card.id for card in active_cards])

    return active_cards

@app.context_processor
def inject_global_counts():
    pending_import_count = 0
    manual_review_count = 0
    ai_import_action_count = 0

    try:
        pending_import_count = CardImportStaging.query.filter(
            CardImportStaging.ai_status == "Pending Review"
        ).count()

        manual_review_count = CardImportStaging.query.filter(
            CardImportStaging.ai_status == "Needs Manual Review"
        ).count()

        ai_import_action_count = pending_import_count + manual_review_count
    except Exception:
        pending_import_count = 0
        manual_review_count = 0
        ai_import_action_count = 0

    try:
        image_inbox_count = ImageInbox.query.filter(ImageInbox.status == "Available").count()
    except Exception:
        image_inbox_count = 0

    return {
        "deal_cart_count": sum(
            (card.quantity or 1)
            for card in get_deal_cart_cards()
        ),
        "pending_import_count": pending_import_count,
        "manual_review_count": manual_review_count,
        "ai_import_action_count": ai_import_action_count,
        "image_inbox_count": image_inbox_count
    }


def get_deal_cart_quantity():
    """Return total card quantity represented by active deal-cart records."""
    return sum((card.quantity or 1) for card in get_deal_cart_cards())


@app.route("/")
def dashboard():
    today_value = date.today()
    today = today_value.isoformat()

    recent_sales_range = request.args.get("recent_sales_range", "30d")
    purchase_summary_range = request.args.get("purchase_summary_range", "30d")

    recent_sales_range_days = {
    "today": 0,
    "3d": 3,
    "7d": 6,
    "30d": 29,
    }

    recent_sales_range_labels = {
        "today": "Today",
        "3d": "Last 3 Days",
        "7d": "Last 7 Days",
        "30d": "Last 30 Days",
    }

    if recent_sales_range not in recent_sales_range_days:
        recent_sales_range = "3d"

    recent_sales_start_date_value = (
        today_value - timedelta(days=recent_sales_range_days[recent_sales_range])
    )
    recent_sales_start_date = recent_sales_start_date_value.isoformat()

    recent_sales_label = recent_sales_range_labels[recent_sales_range]

    if purchase_summary_range not in recent_sales_range_days:
        purchase_summary_range = "30d"

    purchase_summary_start_date_value = (
        today_value - timedelta(days=recent_sales_range_days[purchase_summary_range])
    )
    purchase_summary_start_date = purchase_summary_start_date_value.isoformat()
    purchase_summary_label = recent_sales_range_labels[purchase_summary_range]

    cards = Card.query.all()

    active_cards = [
        card for card in cards
        if card.status != "Sold"
    ]

    sold_cards_all_time = [
        card for card in cards
        if card.status == "Sold"
    ]

    sales_range_cards = [
        card for card in sold_cards_all_time
        if parse_card_date(card.sold_date)
        and parse_card_date(card.sold_date) >= recent_sales_start_date_value
    ]

    sold_cards_today = [
        card for card in sold_cards_all_time
        if parse_card_date(card.sold_date) == today_value
    ]

    recent_sales = list(sales_range_cards)

    recent_sales = sorted(
        recent_sales,
        key=lambda card: (parse_card_date(card.sold_date) or date.min, card.id or 0),
        reverse=True
    )[:12]

    dealer_inventory_active_available = [
        card for card in active_cards
        if card.collection_type == "Inventory"
        and card.status == "Active"
    ]

    dealer_inventory_holding = [
        card for card in active_cards
        if card.collection_type == "Inventory"
        and card.status == "Holding"
    ]

    personal_collection = [
        card for card in active_cards
        if card.collection_type == "Personal Collection"
    ]

    grading_queue = [
        card for card in active_cards
        if card.collection_type == "Grading Queue"
    ]

    trade_bait = [
        card for card in active_cards
        if card.collection_type == "Trade Bait"
    ]

    fulfillment_queue = [
        card for card in sold_cards_all_time
        if getattr(card, "fulfillment_status", None) in ["Needs Pulling", "In Storage"]
        or (
            getattr(card, "fulfillment_status", None) is None
            and card.storage_location
        )
    ]

    pulled_not_shipped_queue = [
        card for card in sold_cards_all_time
        if getattr(card, "fulfillment_status", None) == "Pulled"
    ]

    shipped_not_delivered_queue = [
        card for card in sold_cards_all_time
        if getattr(card, "fulfillment_status", None) == "Shipped"
    ]

    delivered_queue = [
        card for card in sold_cards_all_time
        if getattr(card, "fulfillment_status", None) in ["Delivered", "Completed"]
    ]

    dealer_inventory_cards = sum((card.quantity or 1) for card in dealer_inventory_active_available)
    available_to_sell_cards = dealer_inventory_cards
    inventory_holding_cards = sum((card.quantity or 1) for card in dealer_inventory_holding)
    pc_cards = sum((card.quantity or 1) for card in personal_collection)
    grading_queue_cards = sum((card.quantity or 1) for card in grading_queue)
    trade_bait_cards = sum((card.quantity or 1) for card in trade_bait)
    fulfillment_queue_cards = sum((card.quantity or 1) for card in fulfillment_queue)
    pulled_not_shipped_cards = sum((card.quantity or 1) for card in pulled_not_shipped_queue)
    shipped_not_delivered_cards = sum((card.quantity or 1) for card in shipped_not_delivered_queue)
    delivered_cards = sum((card.quantity or 1) for card in delivered_queue)

    missing_storage_cards = sum(
        (card.quantity or 1)
        for card in active_cards
        if not card.storage_location
    )

    sales_7d_start_date_value = today_value - timedelta(days=6)

    sales_7d_cards = sum(
        (card.quantity or 1)
        for card in sold_cards_all_time
        if parse_card_date(card.sold_date)
        and parse_card_date(card.sold_date) >= sales_7d_start_date_value
    )

    sales_7d_total = sum(
        ((card.sold_price or 0) * (card.quantity or 1))
        for card in sold_cards_all_time
        if parse_card_date(card.sold_date)
        and parse_card_date(card.sold_date) >= sales_7d_start_date_value
    )

    open_workflow_tasks = (
        fulfillment_queue_cards
        + pulled_not_shipped_cards
        + shipped_not_delivered_cards
        + grading_queue_cards
        + inventory_holding_cards
    )

    dealer_inventory_cost = 0
    dealer_inventory_value = 0
    available_asking_price = 0
    available_estimated_value = 0
    available_potential_profit = 0
    pc_total_cost = 0
    pc_estimated_value = 0

    for card in dealer_inventory_active_available:
        quantity = card.quantity or 1
        purchase_cost = card.purchase_price or 0
        estimated_value = card.estimated_value or 0
        asking_price = card.asking_price or 0

        dealer_inventory_cost += purchase_cost * quantity
        dealer_inventory_value += estimated_value * quantity
        available_asking_price += asking_price * quantity
        available_estimated_value += estimated_value * quantity
        available_potential_profit += (asking_price - purchase_cost) * quantity

    for card in personal_collection:
        quantity = card.quantity or 1
        purchase_cost = card.purchase_price or 0
        estimated_value = card.estimated_value or 0

        pc_total_cost += purchase_cost * quantity
        pc_estimated_value += estimated_value * quantity

    dealer_unrealized_gain_loss = (
        dealer_inventory_value - dealer_inventory_cost
    )

    pc_estimated_profit_loss = (
        pc_estimated_value - pc_total_cost
    )

    dealer_unrealized_gain_loss_percent = (
        (dealer_unrealized_gain_loss / dealer_inventory_cost) * 100
        if dealer_inventory_cost
        else 0
    )

    dealer_inventory_value_percent = (
        (dealer_inventory_value / dealer_inventory_cost) * 100
        if dealer_inventory_cost
        else 0
    )

    available_potential_profit_percent = (
        (available_potential_profit / dealer_inventory_cost) * 100
        if dealer_inventory_cost
        else 0
    )

    pc_estimated_profit_loss_percent = (
        (pc_estimated_profit_loss / pc_total_cost) * 100
        if pc_total_cost
        else 0
    )

    pc_estimated_value_percent = (
        (pc_estimated_value / pc_total_cost) * 100
        if pc_total_cost
        else 0
    )

    selected_range_sold_price = 0
    selected_range_sold_cost = 0
    selected_range_profit = 0

    for card in sales_range_cards:
        quantity = card.quantity or 1
        sold_price = card.sold_price or 0
        purchase_cost = card.purchase_price or 0

        selected_range_sold_price += sold_price * quantity
        selected_range_sold_cost += purchase_cost * quantity
        selected_range_profit += (sold_price * quantity) - (purchase_cost * quantity)

    selected_range_sold_cards = sum(
        (card.quantity or 1)
        for card in sales_range_cards
    )

    selected_range_profit_percent = (
        (selected_range_profit / selected_range_sold_cost) * 100
        if selected_range_sold_cost
        else 0
    )

    selected_range_sales_margin_percent = (
        (selected_range_profit / selected_range_sold_price) * 100
        if selected_range_sold_price
        else 0
    )


    # Acquisition activity for the selected dashboard range.
    # This separates newly acquired inventory from cards that were only entered into CardDesk.
    bought_range_cards = [
        card for card in active_cards
        if card.collection_type == "Inventory"
        and card.status == "Active"
        and is_dashboard_acquisition(card)
        and parse_card_date(getattr(card, "acquisition_date", None))
        and parse_card_date(getattr(card, "acquisition_date", None)) >= purchase_summary_start_date_value
    ]

    cards_bought_in_range = sum(
        (card.quantity or 1)
        for card in bought_range_cards
    )

    cost_bought_in_range = sum(
        ((card.purchase_price or 0) * (card.quantity or 1))
        for card in bought_range_cards
    )

    value_bought_in_range = sum(
        ((card.estimated_value or 0) * (card.quantity or 1))
        for card in bought_range_cards
    )

    comp_value_bought_in_range = value_bought_in_range

    # Keep the original template variable names, but make them follow the selected dashboard sales range.
    today_sold_price = selected_range_sold_price
    today_sold_cost = selected_range_sold_cost
    today_profit = selected_range_profit
    today_sold_cards = selected_range_sold_cards
    today_profit_percent = selected_range_profit_percent
    today_sales_margin_percent = selected_range_sales_margin_percent

    rookie_cards = sum(
        (card.quantity or 1)
        for card in active_cards
        if card.is_rookie
    )

    hof_cards = sum(
        (card.quantity or 1)
        for card in active_cards
        if card.is_hof
    )

    raw_cards = sum(
        (card.quantity or 1)
        for card in active_cards
        if card.card_type == "Raw"
    )

    graded_cards = sum(
        (card.quantity or 1)
        for card in active_cards
        if card.card_type == "Graded"
    )

    ai_pending_review_cards = CardImportStaging.query.filter(
        CardImportStaging.ai_status == "Pending Review"
    ).count()

    ai_manual_review_cards = CardImportStaging.query.filter(
        CardImportStaging.ai_status == "Needs Manual Review"
    ).count()

    ai_imported_cards = CardImportStaging.query.filter(
        CardImportStaging.ai_status == "Imported"
    ).count()

    ai_rejected_cards = CardImportStaging.query.filter(
        CardImportStaging.ai_status == "Rejected"
    ).count()

    ai_action_needed_cards = ai_pending_review_cards + ai_manual_review_cards

    mobile_capture_url = url_for("mobile_capture", _external=True)
    mobile_capture_qr_url = (
        "https://api.qrserver.com/v1/create-qr-code/"
        f"?size=220x220&data={quote(mobile_capture_url, safe='')}"
    )

    return render_template(
        "dashboard.html",
        today=today,
        dealer_inventory_cards=dealer_inventory_cards,
        available_to_sell_cards=available_to_sell_cards,
        pc_cards=pc_cards,
        grading_queue_cards=grading_queue_cards,
        trade_bait_cards=trade_bait_cards,
        inventory_holding_cards=inventory_holding_cards,
        fulfillment_queue_cards=fulfillment_queue_cards,
        pulled_not_shipped_cards=pulled_not_shipped_cards,
        shipped_not_delivered_cards=shipped_not_delivered_cards,
        delivered_cards=delivered_cards,
        missing_storage_cards=missing_storage_cards,
        sales_7d_cards=sales_7d_cards,
        sales_7d_total=sales_7d_total,
        open_workflow_tasks=open_workflow_tasks,
        dealer_inventory_cost=dealer_inventory_cost,
        dealer_inventory_value=dealer_inventory_value,
        dealer_inventory_value_percent=dealer_inventory_value_percent,
        dealer_unrealized_gain_loss=dealer_unrealized_gain_loss,
        dealer_unrealized_gain_loss_percent=dealer_unrealized_gain_loss_percent,
        available_asking_price=available_asking_price,
        available_estimated_value=available_estimated_value,
        available_potential_profit=available_potential_profit,
        available_potential_profit_percent=available_potential_profit_percent,
        pc_total_cost=pc_total_cost,
        pc_estimated_value=pc_estimated_value,
        pc_estimated_value_percent=pc_estimated_value_percent,
        pc_estimated_profit_loss=pc_estimated_profit_loss,
        pc_estimated_profit_loss_percent=pc_estimated_profit_loss_percent,
        rookie_cards=rookie_cards,
        hof_cards=hof_cards,
        raw_cards=raw_cards,
        graded_cards=graded_cards,
        sold_cards=today_sold_cards,
        total_sold_price=today_sold_price,
        total_profit=today_profit,
        today_profit_percent=today_profit_percent,
        today_sales_margin_percent=today_sales_margin_percent,
        cards_bought_in_range=cards_bought_in_range,
        cost_bought_in_range=cost_bought_in_range,
        value_bought_in_range=value_bought_in_range,
        comp_value_bought_in_range=comp_value_bought_in_range,
        purchase_summary_range=purchase_summary_range,
        purchase_summary_label=purchase_summary_label,
        purchase_summary_start_date=purchase_summary_start_date,
        recent_sales=recent_sales,
        recent_sales_start_date=recent_sales_start_date,
        recent_sales_range=recent_sales_range,
        recent_sales_label=recent_sales_label,
        sales_summary_label=recent_sales_label,
        sales_summary_range=recent_sales_range,
        sales_summary_start_date=recent_sales_start_date,
        deal_cart_count=get_deal_cart_quantity(),
        ai_pending_review_cards=ai_pending_review_cards,
        ai_manual_review_cards=ai_manual_review_cards,
        ai_imported_cards=ai_imported_cards,
        ai_rejected_cards=ai_rejected_cards,
        ai_action_needed_cards=ai_action_needed_cards,
        mobile_capture_url=mobile_capture_url,
        mobile_capture_qr_url=mobile_capture_qr_url
    )

@app.route("/storage")
def storage_explorer():
    storage_summary = get_storage_summary()

    total_locations = len(storage_summary)
    total_cards = sum(item["total_cards"] for item in storage_summary)
    total_purchase_cost = sum(item["purchase_cost"] for item in storage_summary)
    total_estimated_value = sum(item["estimated_value"] for item in storage_summary)

    return render_template(
        "storage.html",
        storage_summary=storage_summary,
        total_locations=total_locations,
        total_cards=total_cards,
        total_purchase_cost=total_purchase_cost,
        total_estimated_value=total_estimated_value
    )


@app.route("/cards")
def cards():
    sold_range = request.args.get("sold_range")
    search_query = request.args.get("q", "")
    sport_filter = request.args.get("sport", "")
    status_filter = request.args.get("status", "")
    collection_type_filter = request.args.get("collection_type", "")
    rookie_filter = request.args.get("rookie", "")
    hof_filter = request.args.get("hof", "")
    card_type_filter = request.args.get("card_type", "")
    grade_estimate_filter = request.args.get("grade_estimate", "")
    actual_grade_filter = request.args.get("actual_grade", "")
    year_filter = request.args.get("year", "")
    brand_filter = request.args.get("brand", "")
    storage_filter = request.args.get("storage", "")
    variation_filter = request.args.get("variation", "")
    acquisition_source_filter = request.args.get("acquisition_source", "")
    acquisition_event_filter = request.args.get("acquisition_event", "")
    min_price = request.args.get("min_price", "")
    max_price = request.args.get("max_price", "")
    scope = request.args.get("scope", "inventory")

    query = Card.query

    # Default inventory view should show cards that are actually available to sell.
    # If the user chooses filters, those filters take control.
    has_manual_scope_filter = any([
        sold_range,
        status_filter,
        collection_type_filter,
    ])

    if scope == "inventory" and not has_manual_scope_filter:
        query = query.filter(Card.status == "Active")
        query = query.filter(Card.collection_type == "Inventory")

    # Do not apply LIMIT until after all filters have been added.
    # SQLAlchemy raises an InvalidRequestError if .filter() is called after .limit().

    if search_query:
        query = query.filter(
            db.or_(
                Card.card_code.ilike(f"%{search_query}%"),
                Card.player_name.ilike(f"%{search_query}%"),
                Card.sport.ilike(f"%{search_query}%"),
                Card.brand.ilike(f"%{search_query}%"),
                Card.set_name.ilike(f"%{search_query}%"),
                Card.card_number.ilike(f"%{search_query}%"),
                Card.variation.ilike(f"%{search_query}%"),
                Card.grade_estimate.ilike(f"%{search_query}%"),
                Card.actual_grade.ilike(f"%{search_query}%"),
                Card.grading_company.ilike(f"%{search_query}%"),
                Card.cert_number.ilike(f"%{search_query}%"),
                Card.status.ilike(f"%{search_query}%"),
                Card.collection_type.ilike(f"%{search_query}%"),
                Card.storage_location.ilike(f"%{search_query}%"),
                Card.acquisition_source.ilike(f"%{search_query}%"),
                Card.acquisition_event.ilike(f"%{search_query}%")
            )
        )

    if rookie_filter == "yes":
        query = query.filter(Card.is_rookie == True)

    if rookie_filter == "no":
        query = query.filter(Card.is_rookie == False)

    if hof_filter == "yes":
        query = query.filter(Card.is_hof == True)

    if hof_filter == "no":
        query = query.filter(Card.is_hof == False)

    if card_type_filter:
        query = query.filter(Card.card_type == card_type_filter)

    if sport_filter:
        query = query.filter(Card.sport == sport_filter)

    if status_filter:
        query = query.filter(Card.status == status_filter)

    if collection_type_filter:
        query = query.filter(Card.collection_type == collection_type_filter)

    if acquisition_source_filter:
        query = query.filter(Card.acquisition_source == acquisition_source_filter)

    if acquisition_event_filter:
        query = query.filter(Card.acquisition_event.ilike(f"%{acquisition_event_filter}%"))

    if grade_estimate_filter:
        query = query.filter(Card.grade_estimate.ilike(f"%{grade_estimate_filter}%"))

    if actual_grade_filter:
        query = query.filter(Card.actual_grade.ilike(f"%{actual_grade_filter}%"))

    if year_filter:
        try:
            query = query.filter(Card.year == int(year_filter))
        except ValueError:
            year_filter = ""

    if brand_filter:
        query = query.filter(Card.brand.ilike(f"%{brand_filter}%"))

    if storage_filter == "__missing__":
        query = query.filter(db.or_(Card.storage_location.is_(None), Card.storage_location == ""))
    elif storage_filter:
        query = query.filter(Card.storage_location.ilike(f"%{storage_filter}%"))

    if variation_filter:
        query = query.filter(Card.variation.ilike(f"%{variation_filter}%"))

    if min_price:
        try:
            query = query.filter(Card.purchase_price >= float(min_price))
        except ValueError:
            min_price = ""

    if max_price:
        try:
            query = query.filter(Card.purchase_price <= float(max_price))
        except ValueError:
            max_price = ""


    if sold_range:
        today_value = date.today()

        if sold_range == "today":
            start_date = today_value
        elif sold_range == "3d":
            start_date = today_value - timedelta(days=3)
        elif sold_range == "7d":
            start_date = today_value - timedelta(days=6)
        elif sold_range == "30d":
            start_date = today_value - timedelta(days=29)
        else:
            start_date = None

        if start_date:
            query = query.filter(Card.sold_date >= start_date.isoformat())

    has_active_filter = any([
        sold_range,
        search_query,
        sport_filter,
        status_filter,
        collection_type_filter,
        rookie_filter,
        hof_filter,
        card_type_filter,
        grade_estimate_filter,
        actual_grade_filter,
        year_filter,
        brand_filter,
        storage_filter,
        variation_filter,
        acquisition_source_filter,
        acquisition_event_filter,
        min_price,
        max_price,
    ])

    query = query.order_by(Card.id.desc())

    # Keep the unfiltered All Records view from loading the entire database,
    # but allow searches/filters inside All Records to search the full dataset.
    if scope == "all" and not has_active_filter:
        query = query.limit(250)

    all_cards = query.all()

    # Results should summarize the cards currently displayed on the inventory page.
    summary_cards = all_cards

    filtered_card_count = sum(
        (card.quantity or 1)
        for card in summary_cards
    )

    filtered_total_cost = sum(
        (card.purchase_price or 0) * (card.quantity or 1)
        for card in summary_cards
    )

    filtered_total_asking = sum(
        (card.asking_price or 0) * (card.quantity or 1)
        for card in summary_cards
    )

    filtered_total_sold = sum(
        (card.sold_price or 0) * (card.quantity or 1)
        for card in summary_cards
    )

    filtered_total_profit = sum(
        ((card.sold_price or 0) - (card.purchase_price or 0)) * (card.quantity or 1)
        for card in summary_cards
    )
    active_inventory_count = sum(
        (card.quantity or 1)
        for card in Card.query
        .filter(Card.status == "Active")
        .filter(Card.collection_type == "Inventory")
        .all()
    )

    missing_storage_count = sum(
        (card.quantity or 1)
        for card in Card.query
        .filter(Card.status == "Active")
        .filter(Card.collection_type == "Inventory")
        .filter(db.or_(Card.storage_location.is_(None), Card.storage_location == ""))
        .all()
    )

    storage_locations = get_storage_locations()

    acquisition_sources = [
        "Existing Inventory",
        "Cash Purchase",
        "Trade-In",
        "Bulk Collection",
        "Pack Pull",
        "Personal Collection",
        "Other",
    ]

    acquisition_events = [
        row[0]
        for row in db.session.query(Card.acquisition_event)
        .filter(Card.acquisition_event.isnot(None))
        .filter(Card.acquisition_event != "")
        .distinct()
        .order_by(Card.acquisition_event.asc())
        .all()
    ]

    deal_cart_ids = get_deal_cart_ids()
    deal_cart_count = get_deal_cart_quantity()

    return render_template(
        "card_list.html",
        cards=all_cards,
        filtered_card_count=filtered_card_count,
        filtered_total_cost=filtered_total_cost,
        filtered_total_asking=filtered_total_asking,
        filtered_total_sold=filtered_total_sold,
        filtered_total_profit=filtered_total_profit,
        search_query=search_query,
        sport_filter=sport_filter,
        status_filter=status_filter,
        collection_type_filter=collection_type_filter,
        rookie_filter=rookie_filter,
        hof_filter=hof_filter,
        card_type_filter=card_type_filter,
        grade_estimate_filter=grade_estimate_filter,
        actual_grade_filter=actual_grade_filter,
        year_filter=year_filter,
        brand_filter=brand_filter,
        storage_filter=storage_filter,
        variation_filter=variation_filter,
        acquisition_source_filter=acquisition_source_filter,
        acquisition_event_filter=acquisition_event_filter,
        min_price=min_price,
        max_price=max_price,
        storage_locations=storage_locations,
        acquisition_sources=acquisition_sources,
        acquisition_events=acquisition_events,
        deal_cart_ids=deal_cart_ids,
        deal_cart_count=deal_cart_count,
        active_inventory_count=active_inventory_count,
        missing_storage_count=missing_storage_count,
        scope=scope
    )


@app.route("/cards/<int:card_id>")
def card_detail(card_id):
    card = Card.query.get_or_404(card_id)

    return render_template(
        "card_detail.html",
        card=card
    )


@app.route("/cards/<int:card_id>/quick-sell", methods=["GET", "POST"])
def quick_sell(card_id):
    card = Card.query.get_or_404(card_id)

    if request.method == "POST":
        card.sold_price = request.form.get("sold_price") or None
        card.sold_date = request.form.get("sold_date") or date.today().isoformat()
        card.sales_platform = clean_value(request.form.get("sales_platform"))
        card.status = "Sold"
        card.fulfillment_status = "Needs Pulling" if card.storage_location else "No Location"

        if card.storage_location:
            existing_notes = card.notes or ""
            pull_note = f"Needs pulling from: {card.storage_location}"
            card.notes = (existing_notes + "\n" if existing_notes else "") + pull_note

        db.session.commit()

        flash(f"{card.card_code} marked as sold.")

        return redirect(url_for("card_detail", card_id=card.id))

    return render_template(
        "quick_sell.html",
        card=card,
        today=date.today().isoformat()
    )


@app.route("/deal-cart")
def deal_cart():
    selected_cards = get_deal_cart_cards()

    selected_card_count = sum((card.quantity or 1) for card in selected_cards)
    total_cost = sum((card.purchase_price or 0) * (card.quantity or 1) for card in selected_cards)
    total_asking = sum((card.asking_price or 0) * (card.quantity or 1) for card in selected_cards)
    total_estimated_value = sum((card.estimated_value or 0) * (card.quantity or 1) for card in selected_cards)
    total_estimated_profit_loss = total_estimated_value - total_cost

    return render_template(
        "deal_cart.html",
        selected_cards=selected_cards,
        selected_card_count=selected_card_count,
        total_cost=total_cost,
        total_asking=total_asking,
        total_estimated_value=total_estimated_value,
        total_estimated_profit_loss=total_estimated_profit_loss,
        today=date.today().isoformat()
    )


@app.route("/deal-cart/add", methods=["POST"])
def add_to_deal_cart():
    selected_ids = request.form.getlist("card_ids")

    if not selected_ids:
        flash("Select at least one Active card to add to the deal cart.")
        return redirect(request.referrer or url_for("cards"))

    clean_selected_ids = []

    for card_id in selected_ids:
        try:
            clean_selected_ids.append(int(card_id))
        except (TypeError, ValueError):
            continue

    active_cards = (
        Card.query
        .filter(Card.id.in_(clean_selected_ids))
        .filter(Card.status == "Active")
        .all()
    )

    if not active_cards:
        flash("Only Active cards can be added to the deal cart.")
        return redirect(request.referrer or url_for("cards"))

    existing_ids = get_deal_cart_ids()

    for card in active_cards:
        if card.id not in existing_ids:
            existing_ids.append(card.id)

    save_deal_cart_ids(existing_ids)

    flash(f"Added {len(active_cards)} active card(s) to the deal cart.")

    return redirect(request.referrer or url_for("cards"))


@app.route("/deal-cart/remove/<int:card_id>", methods=["POST"])
def remove_from_deal_cart(card_id):
    remaining_ids = [existing_id for existing_id in get_deal_cart_ids() if existing_id != card_id]
    save_deal_cart_ids(remaining_ids)

    flash("Card removed from deal cart.")

    return redirect(url_for("deal_cart"))


@app.route("/deal-cart/clear", methods=["POST"])
def clear_deal_cart():
    save_deal_cart_ids([])

    flash("Deal cart cleared.")

    return redirect(request.referrer or url_for("cards"))


@app.route("/bulk-sell", methods=["POST"])
def bulk_sell():
    card_ids = request.form.getlist("card_ids")

    if not card_ids:
        flash("Select at least one card to sell as a lot.")
        return redirect(url_for("cards"))

    selected_cards = (
        Card.query
        .filter(Card.id.in_([int(card_id) for card_id in card_ids]))
        .order_by(Card.player_name.asc())
        .all()
    )

    selected_cards = [card for card in selected_cards if card.status == "Active"]

    if not selected_cards:
        flash("Selected cards must be Active before they can be sold.")
        return redirect(url_for("cards"))

    if request.form.get("total_sale_price"):
        total_sale_price = float(request.form.get("total_sale_price") or 0)
        trade_credit = float(request.form.get("trade_credit") or 0)
        discount_percent = float(request.form.get("discount_percent") or 0)
        sold_date = request.form.get("sold_date") or date.today().isoformat()
        sales_platform = clean_value(request.form.get("sales_platform"))
        customer_name = clean_value(request.form.get("customer_name"))
        payment_type = clean_value(request.form.get("payment_type"))
        deal_notes = clean_value(request.form.get("deal_notes"))
        mark_pulled_now = request.form.get("mark_pulled_now") == "1"
        fulfillment_status = "Pulled" if mark_pulled_now else "Needs Pulling"

        total_asking = sum((card.asking_price or 0) * (card.quantity or 1) for card in selected_cards)
        total_quantity = sum((card.quantity or 1) for card in selected_cards)

        if total_asking > 0:
            for card in selected_cards:
                quantity = card.quantity or 1
                card_asking_total = (card.asking_price or 0) * quantity
                card_share = card_asking_total / total_asking
                card.sold_price = total_sale_price * card_share / quantity
                card.sold_date = sold_date
                card.sales_platform = sales_platform
                card.status = "Sold"
                card.fulfillment_status = fulfillment_status

                note_parts = []
                if customer_name:
                    note_parts.append(f"Customer: {customer_name}")
                if payment_type:
                    note_parts.append(f"Payment: {payment_type}")
                if trade_credit:
                    note_parts.append(f"Trade credit: ${trade_credit:.2f}")
                if card.storage_location and not mark_pulled_now:
                    note_parts.append(f"Needs pulling from: {card.storage_location}")
                if card.storage_location and mark_pulled_now:
                    note_parts.append(f"Pulled from: {card.storage_location}")
                if discount_percent:
                    note_parts.append(f"Deal discount: {discount_percent:.2f}%")
                if deal_notes:
                    note_parts.append(f"Deal notes: {deal_notes}")

                if note_parts:
                    existing_notes = card.notes or ""
                    deal_note_text = " | ".join(note_parts)
                    card.notes = (existing_notes + "\n" if existing_notes else "") + deal_note_text

            split_message = f"split proportionally by asking price with {discount_percent:.2f}% discount"
        else:
            split_price = total_sale_price / total_quantity if total_quantity else 0

            for card in selected_cards:
                card.sold_price = split_price
                card.sold_date = sold_date
                card.sales_platform = sales_platform
                card.status = "Sold"
                card.fulfillment_status = fulfillment_status

            split_message = f"split evenly at ${split_price:.2f} each because no asking prices were available"

        db.session.commit()

        sold_ids = [card.id for card in selected_cards]
        remaining_cart_ids = [card_id for card_id in get_deal_cart_ids() if card_id not in sold_ids]
        save_deal_cart_ids(remaining_cart_ids)

        flash(
            f"Deal completed. {len(selected_cards)} records / {total_quantity} cards marked Sold; ${total_sale_price:.2f} sale amount {split_message}."
        )

        return redirect(url_for("cards"))

    total_cost = sum((card.purchase_price or 0) * (card.quantity or 1) for card in selected_cards)
    total_asking = sum((card.asking_price or 0) * (card.quantity or 1) for card in selected_cards)

    return render_template(
        "bulk_sell.html",
        selected_cards=selected_cards,
        total_cost=total_cost,
        total_asking=total_asking,
        today=date.today().isoformat()
    )


@app.route("/cards/<int:card_id>/edit", methods=["GET", "POST"])
def edit_card(card_id):
    card = Card.query.get_or_404(card_id)

    if request.method == "POST":
        uploaded_image = save_uploaded_image(request.files.get("card_image"))

        if uploaded_image:
            delete_image_file(card.image_filename)
            card.image_filename = uploaded_image

        if request.form.get("remove_image"):
            delete_image_file(card.image_filename)
            card.image_filename = None

        card.card_code = request.form["card_code"]
        card.sport = request.form.get("sport")
        card.player_name = clean_value(request.form["player_name"])
        card.year = request.form.get("year") or None
        card.brand = clean_value(request.form.get("brand"))
        card.set_name = clean_value(request.form.get("set_name"))
        card.card_number = clean_value(request.form.get("card_number"))
        card.variation = clean_value(request.form.get("variation"))
        card.is_rookie = True if request.form.get("is_rookie") else False
        card.is_hof = True if request.form.get("is_hof") else False
        card.card_type = request.form.get("card_type") or "Raw"
        card.grading_company = clean_value(request.form.get("grading_company"))
        card.actual_grade = clean_value(request.form.get("actual_grade"))
        card.cert_number = clean_value(request.form.get("cert_number"))
        card.grade_estimate = clean_value(request.form.get("grade_estimate"))
        card.quantity = int(request.form.get("quantity") or 1)
        card.purchase_price = request.form.get("purchase_price") or None
        card.estimated_value = request.form.get("estimated_value") or None
        card.asking_price = request.form.get("asking_price") or None
        card.sold_price = request.form.get("sold_price") or None
        card.sold_date = request.form.get("sold_date")
        card.sales_platform = clean_value(request.form.get("sales_platform"))
        card.purchase_date = purchase_date_value(request.form)
        card.acquisition_source = acquisition_value(request.form.get("acquisition_source"))
        card.acquisition_date = acquisition_date_value(request.form)
        card.acquisition_event = clean_value(request.form.get("acquisition_event"))
        card.storage_location = clean_value(request.form.get("storage_location"))
        card.collection_type = request.form.get("collection_type") or "Inventory"
        card.status = request.form.get("status")
        card.notes = request.form.get("notes")

        db.session.commit()

        flash("Card updated successfully.")

        return redirect(url_for("card_detail", card_id=card.id))

    return render_template(
        "edit_card.html",
        card=card
    )


@app.route("/cards/<int:card_id>/delete", methods=["POST"])
def delete_card(card_id):
    card = Card.query.get_or_404(card_id)

    delete_image_file(card.image_filename)

    db.session.delete(card)
    db.session.commit()

    flash("Card deleted successfully.")

    return redirect(url_for("cards"))


@app.route("/cards/<int:card_id>/update-storage", methods=["POST"])
def update_card_storage(card_id):
    card = Card.query.get_or_404(card_id)

    card.storage_location = clean_value(request.form.get("storage_location"))

    db.session.commit()

    flash(f"{card.card_code} storage location updated.")

    return redirect(request.referrer or url_for("cards"))




@app.route("/cards/<int:card_id>/update-status", methods=["POST"])
def update_card_status(card_id):
    card = Card.query.get_or_404(card_id)

    new_status = request.form.get("status") or card.status
    card.status = new_status

    db.session.commit()

    flash(f"{card.card_code} status updated to {card.status}.")

    return redirect(request.referrer or url_for("cards"))


@app.route("/cards/<int:card_id>/add-duplicate", methods=["POST"])
def add_duplicate(card_id):
    card = Card.query.get_or_404(card_id)

    old_quantity = card.quantity or 1

    card.quantity = old_quantity + 1

    db.session.commit()

    flash(
        f"Quantity updated from {old_quantity} to {card.quantity}."
    )

    return redirect(url_for("cards"))


@app.route("/cards/<int:card_id>/clone")
def clone_card(card_id):
    source_card = Card.query.get_or_404(card_id)

    flash(
        f"Cloning {source_card.card_code}. Review the details, adjust what changed, then save as a new card."
    )

    return render_template(
        "add_card.html",
        clone_source=source_card,
        recent_added_cards=Card.query.order_by(Card.id.desc()).limit(5).all(),
        image_inbox_items=get_available_inbox_images(limit=24),
        selected_inbox_image_id=request.args.get("inbox_image_id", type=int)
    )


@app.route("/rapid-entry", methods=["GET", "POST"])
def rapid_entry():
    if request.method == "POST":
        quantity_to_add = int(request.form.get("quantity") or 1)
        card_type = request.form.get("card_type") or "Raw"
        collection_type = request.form.get("collection_type") or "Inventory"

        player_name = clean_value(request.form["player_name"])
        sport = request.form.get("sport")
        year_value = request.form.get("year")
        brand = clean_value(request.form.get("brand"))
        set_name = clean_value(request.form.get("set_name"))
        card_number = clean_value(request.form.get("card_number"))
        variation = clean_value(request.form.get("variation"))
        force_new_card = request.form.get("force_new") == "1"

        existing_query = Card.query.filter(
            Card.player_name.ilike(player_name),
            Card.sport == sport,
            Card.card_type == card_type
        )

        if year_value:
            existing_query = existing_query.filter(Card.year == int(year_value))

        if brand:
            existing_query = existing_query.filter(Card.brand.ilike(brand))

        if card_number:
            existing_query = existing_query.filter(Card.card_number.ilike(card_number))

        if variation:
            existing_query = existing_query.filter(Card.variation.ilike(variation))
        else:
            existing_query = existing_query.filter(
                db.or_(Card.variation.is_(None), Card.variation == "")
            )

        existing_card = existing_query.first()

        if existing_card and not force_new_card:
            old_quantity = existing_card.quantity or 1
            existing_card.quantity = old_quantity + quantity_to_add
            existing_card.collection_type = collection_type
            existing_card.acquisition_source = existing_card.acquisition_source or acquisition_value(request.form.get("acquisition_source"))
            existing_card.acquisition_date = existing_card.acquisition_date or acquisition_date_value(request.form)
            existing_card.acquisition_event = existing_card.acquisition_event or clean_value(request.form.get("acquisition_event"))
            db.session.commit()
            flash(f"Duplicate found. Quantity updated from {old_quantity} to {existing_card.quantity}.")
            saved_card_id = existing_card.id
        else:
            new_card = Card(
                card_code=generate_card_code(),
                sport=sport,
                player_name=player_name,
                year=year_value or None,
                brand=brand,
                set_name=set_name,
                card_number=card_number,
                variation=variation,
                is_rookie=True if request.form.get("is_rookie") else False,
                is_hof=True if request.form.get("is_hof") else False,
                card_type=card_type,
                grading_company=clean_value(request.form.get("grading_company")),
                actual_grade=clean_value(request.form.get("actual_grade")),
                cert_number=clean_value(request.form.get("cert_number")),
                grade_estimate=clean_value(request.form.get("grade_estimate")),
                quantity=quantity_to_add,
                purchase_price=request.form.get("purchase_price") or None,
                estimated_value=request.form.get("estimated_value") or None,
                asking_price=request.form.get("asking_price") or None,
                sold_price=request.form.get("sold_price") or None,
                sold_date=request.form.get("sold_date"),
                sales_platform=clean_value(request.form.get("sales_platform")),
                purchase_date=purchase_date_value(request.form),
                acquisition_source=acquisition_value(request.form.get("acquisition_source")),
                acquisition_date=acquisition_date_value(request.form),
                acquisition_event=clean_value(request.form.get("acquisition_event")),
                storage_location=clean_value(request.form.get("storage_location")),
                collection_type=collection_type,
                notes=request.form.get("notes"),
                status=request.form.get("status") or "Active"
            )

            db.session.add(new_card)
            db.session.commit()
            flash("Rapid entry card saved.")
            saved_card_id = new_card.id

        submit_action = request.form.get("submit_action")

        if submit_action == "save_view":
            return redirect(url_for("card_detail", card_id=saved_card_id))

        keep_values = {
            "sport": sport or "",
            "year": year_value or "",
            "brand": brand or "",
            "set_name": set_name or "",
            "card_type": card_type or "Raw",
            "storage_location": request.form.get("storage_location") or "",
            "collection_type": collection_type or "Inventory",
            "status": request.form.get("status") or "Active",
            "purchase_date": purchase_date_value(request.form) or "",
            "acquisition_source": request.form.get("acquisition_source") or "Existing Inventory",
            "acquisition_date": acquisition_date_value(request.form) or "",
            "acquisition_event": request.form.get("acquisition_event") or ""
        }

        return redirect(url_for("rapid_entry", **keep_values))

    return render_template("rapid_entry.html")


@app.route("/add-card", methods=["GET", "POST"])
def add_card():
    if request.method == "POST":
        quantity_to_add = int(request.form.get("quantity") or 1)

        card_type = request.form.get("card_type") or "Raw"
        collection_type = request.form.get("collection_type") or "Inventory"
        card_status = "Holding" if collection_type == "Personal Collection" else "Active"

        player_name = clean_value(request.form["player_name"])
        sport = request.form.get("sport")
        year_value = request.form.get("year")
        brand = clean_value(request.form.get("brand"))
        card_number = clean_value(request.form.get("card_number"))
        variation = clean_value(request.form.get("variation"))
        uploaded_image = save_uploaded_image(request.files.get("card_image"))
        selected_inbox_image = None
        selected_inbox_image_id = request.form.get("inbox_image_id")

        if not uploaded_image and selected_inbox_image_id:
            try:
                selected_inbox_image = ImageInbox.query.get(int(selected_inbox_image_id))
            except (TypeError, ValueError):
                selected_inbox_image = None

            if selected_inbox_image and selected_inbox_image.status == "Available":
                uploaded_image = selected_inbox_image.image_filename
            else:
                selected_inbox_image = None
                flash("Selected Image Inbox image is no longer available.")

        force_new_card = request.form.get("force_new") == "1"

        existing_query = Card.query.filter(
            Card.player_name.ilike(player_name),
            Card.sport == sport,
            Card.card_type == card_type
        )

        if year_value:
            existing_query = existing_query.filter(
                Card.year == int(year_value)
            )

        if brand:
            existing_query = existing_query.filter(
                Card.brand.ilike(brand)
            )

        if card_number:
            existing_query = existing_query.filter(
                Card.card_number.ilike(card_number)
            )

        if variation:
            existing_query = existing_query.filter(
                Card.variation.ilike(variation)
            )
        else:
            existing_query = existing_query.filter(
                db.or_(
                    Card.variation.is_(None),
                    Card.variation == ""
                )
            )

        existing_card = existing_query.first()

        if existing_card and not force_new_card:
            old_quantity = existing_card.quantity or 1

            existing_card.quantity = old_quantity + quantity_to_add
            existing_card.collection_type = collection_type
            existing_card.status = card_status

            if uploaded_image:
                if existing_card.image_filename and existing_card.image_filename != uploaded_image:
                    delete_image_file(existing_card.image_filename)

                existing_card.image_filename = uploaded_image

            if selected_inbox_image:
                mark_inbox_image_used(selected_inbox_image, existing_card.id)

            db.session.commit()

            flash(
                f"Duplicate card found. Quantity updated from {old_quantity} to {existing_card.quantity}."
            )

            return redirect(
                url_for(
                    "card_detail",
                    card_id=existing_card.id
                )
            )

        new_card = Card(
            card_code=generate_card_code(),
            sport=sport,
            player_name=player_name,
            year=year_value or None,
            brand=brand,
            set_name=clean_value(request.form.get("set_name")),
            card_number=card_number,
            variation=variation,
            is_rookie=True if request.form.get("is_rookie") else False,
            is_hof=True if request.form.get("is_hof") else False,
            card_type=card_type,
            grading_company=clean_value(
                request.form.get("grading_company")
            ),
            actual_grade=clean_value(
                request.form.get("actual_grade")
            ),
            cert_number=clean_value(
                request.form.get("cert_number")
            ),
            grade_estimate=clean_value(
                request.form.get("grade_estimate")
            ),
            quantity=quantity_to_add,
            purchase_price=request.form.get("purchase_price") or None,
            estimated_value=request.form.get("estimated_value") or None,
            asking_price=request.form.get("asking_price") or None,
            sold_price=request.form.get("sold_price") or None,
            sold_date=request.form.get("sold_date"),
            sales_platform=clean_value(request.form.get("sales_platform")),
            purchase_date=purchase_date_value(request.form),
            acquisition_source=acquisition_value(request.form.get("acquisition_source")),
            acquisition_date=acquisition_date_value(request.form),
            acquisition_event=clean_value(request.form.get("acquisition_event")),
            storage_location=clean_value(
                request.form.get("storage_location")
            ),
            collection_type=collection_type,
            image_filename=uploaded_image,
            notes=request.form.get("notes"),
            status=card_status
        )

        db.session.add(new_card)
        db.session.flush()

        if selected_inbox_image:
            mark_inbox_image_used(selected_inbox_image, new_card.id)

        db.session.commit()

        if force_new_card:
            flash("Cloned card saved as a new inventory record.")
        else:
            flash("New card added successfully.")

        return redirect(url_for("card_detail", card_id=new_card.id))

    recent_added_cards = Card.query.order_by(Card.id.desc()).limit(5).all()
    image_inbox_items = get_available_inbox_images(limit=24)

    return render_template(
        "add_card.html",
        clone_source=None,
        recent_added_cards=recent_added_cards,
        image_inbox_items=image_inbox_items,
        selected_inbox_image_id=request.args.get("inbox_image_id", type=int)
    )



@app.route("/ai-import", methods=["GET", "POST"])
def ai_import_upload():
    """Upload one or more card images and stage Ximilar recognition results."""
    if request.method == "POST":
        uploaded_files = request.files.getlist("card_images")
        uploaded_files = [file for file in uploaded_files if file and file.filename]

        if not uploaded_files:
            flash("Choose at least one card image to scan.")
            return redirect(url_for("ai_import_upload"))

        staged_count = 0
        error_count = 0

        for uploaded_file in uploaded_files:
            image_filename, source_filename = save_uploaded_image_with_source(uploaded_file)

            if not image_filename:
                error_count += 1
                continue

            staged_card = CardImportStaging(
                image_filename=image_filename,
                source_filename=source_filename,
                sport=request.form.get("default_sport") or "Baseball",
                collection_type=request.form.get("collection_type") or "Inventory",
                status=request.form.get("status") or "Active",
                purchase_date=purchase_date_value(request.form),
                acquisition_source=acquisition_value(request.form.get("acquisition_source")),
                acquisition_date=acquisition_date_value(request.form),
                acquisition_event=clean_value(request.form.get("acquisition_event")),
                storage_location=clean_value(request.form.get("storage_location")),
                quantity=1,
                ai_status="Pending Review",
            )

            try:
                raw_response = call_ximilar_for_image(image_filename)
                extracted = extract_card_data_from_ximilar(raw_response)

                staged_card.raw_response_json = json.dumps(raw_response, indent=2, sort_keys=True)
                staged_card.player_name = clean_value(extracted.get("player_name"))
                staged_card.year = extracted.get("year")
                staged_card.sport = extracted.get("sport") or staged_card.sport
                staged_card.brand = clean_value(extracted.get("brand"))
                staged_card.set_name = clean_value(extracted.get("set_name"))
                staged_card.card_number = clean_value(extracted.get("card_number"))
                staged_card.variation = clean_value(extracted.get("variation"))
                staged_card.card_type = extracted.get("card_type") or "Raw"
                staged_card.grading_company = clean_value(extracted.get("grading_company"))
                staged_card.actual_grade = clean_value(extracted.get("actual_grade"))
                staged_card.cert_number = clean_value(extracted.get("cert_number"))
                staged_card.ai_confidence = extracted.get("ai_confidence")
            except Exception as error:
                staged_card.ai_status = "Needs Manual Review"
                staged_card.ai_error = str(error)
                error_count += 1

            db.session.add(staged_card)
            staged_count += 1

        db.session.commit()

        if staged_count:
            flash(f"{staged_count} image(s) added to the AI review queue.")
        if error_count:
            flash(f"{error_count} image(s) need manual review because Ximilar did not return usable data.")

        return redirect(url_for("ai_import_review"))

    pending_count = CardImportStaging.query.filter(CardImportStaging.ai_status.in_(["Pending Review", "Needs Manual Review"])).count()
    imported_count = CardImportStaging.query.filter(CardImportStaging.ai_status == "Imported").count()

    return render_template(
        "ai_import_upload.html",
        pending_count=pending_count,
        imported_count=imported_count,
        token_configured=bool(XIMILAR_API_TOKEN),
    )


@app.route("/ai-import/review")
def ai_import_review():
    status_filter = request.args.get("status", "Pending Review")

    query = CardImportStaging.query

    if status_filter == "All":
        pass
    elif status_filter == "Needs Manual Review":
        query = query.filter(CardImportStaging.ai_status == "Needs Manual Review")
    elif status_filter == "Imported":
        query = query.filter(CardImportStaging.ai_status == "Imported")
    elif status_filter == "Rejected":
        query = query.filter(CardImportStaging.ai_status == "Rejected")
    else:
        status_filter = "Pending Review"
        query = query.filter(CardImportStaging.ai_status == "Pending Review")

    staged_cards = query.order_by(CardImportStaging.created_at.desc(), CardImportStaging.id.desc()).all()

    focus_id = request.args.get("focus", type=int)
    missing_fields = [
        field.strip()
        for field in request.args.get("missing", "").split(",")
        if field.strip()
    ]
    imported_success = request.args.get("imported") == "1"

    if focus_id:
        staged_cards = sorted(
            staged_cards,
            key=lambda card: 0 if card.id == focus_id else 1
        )

    duplicate_map = {card.id: find_probable_duplicate_from_staging(card) for card in staged_cards}

    counts = {
        "pending": CardImportStaging.query.filter(CardImportStaging.ai_status == "Pending Review").count(),
        "manual": CardImportStaging.query.filter(CardImportStaging.ai_status == "Needs Manual Review").count(),
        "imported": CardImportStaging.query.filter(CardImportStaging.ai_status == "Imported").count(),
        "rejected": CardImportStaging.query.filter(CardImportStaging.ai_status == "Rejected").count(),
    }

    return render_template(
        "ai_import_review.html",
        staged_cards=staged_cards,
        duplicate_map=duplicate_map,
        status_filter=status_filter,
        counts=counts,
        focus_id=focus_id,
        missing_fields=missing_fields,
        imported_success=imported_success,
    )


@app.route("/ai-import/<int:staging_id>/update", methods=["POST"])
def update_staged_import(staging_id):
    staged_card = CardImportStaging.query.get_or_404(staging_id)

    staged_card.player_name = clean_value(request.form.get("player_name"))
    staged_card.sport = request.form.get("sport") or "Baseball"
    staged_card.year = normalize_year(request.form.get("year"))
    staged_card.brand = clean_value(request.form.get("brand"))
    staged_card.set_name = clean_value(request.form.get("set_name"))
    staged_card.card_number = clean_value(request.form.get("card_number"))
    staged_card.variation = clean_value(request.form.get("variation"))
    staged_card.is_rookie = True if request.form.get("is_rookie") else False
    staged_card.is_hof = True if request.form.get("is_hof") else False
    staged_card.card_type = request.form.get("card_type") or "Raw"
    staged_card.grading_company = clean_value(request.form.get("grading_company"))
    staged_card.actual_grade = clean_value(request.form.get("actual_grade"))
    staged_card.cert_number = clean_value(request.form.get("cert_number"))
    staged_card.grade_estimate = clean_value(request.form.get("grade_estimate"))
    staged_card.quantity = int(request.form.get("quantity") or 1)
    staged_card.purchase_price = request.form.get("purchase_price") or None
    staged_card.estimated_value = request.form.get("estimated_value") or None
    staged_card.asking_price = request.form.get("asking_price") or None
    staged_card.purchase_date = purchase_date_value(request.form)
    staged_card.acquisition_source = acquisition_value(request.form.get("acquisition_source"))
    staged_card.acquisition_date = acquisition_date_value(request.form)
    staged_card.acquisition_event = clean_value(request.form.get("acquisition_event"))
    staged_card.storage_location = clean_value(request.form.get("storage_location"))
    staged_card.collection_type = request.form.get("collection_type") or "Inventory"
    staged_card.status = request.form.get("status") or "Active"
    staged_card.notes = request.form.get("notes")

    if staged_card.ai_status not in ["Imported", "Rejected"]:
        staged_card.ai_status = "Pending Review"

    db.session.commit()
    flash("AI import draft updated.")

    return redirect(url_for("ai_import_review", status=request.args.get("status", "Pending Review")))



def apply_staged_import_form(staged_card, form_data):
    """Apply review form values to a staged card before validation/import."""
    staged_card.player_name = clean_value(form_data.get("player_name"))
    staged_card.sport = form_data.get("sport") or "Baseball"
    staged_card.year = normalize_year(form_data.get("year"))
    staged_card.brand = clean_value(form_data.get("brand"))
    staged_card.set_name = clean_value(form_data.get("set_name"))
    staged_card.card_number = clean_value(form_data.get("card_number"))
    staged_card.variation = clean_value(form_data.get("variation"))
    staged_card.is_rookie = True if form_data.get("is_rookie") else False
    staged_card.is_hof = True if form_data.get("is_hof") else False
    staged_card.card_type = form_data.get("card_type") or "Raw"
    staged_card.grading_company = clean_value(form_data.get("grading_company"))
    staged_card.actual_grade = clean_value(form_data.get("actual_grade"))
    staged_card.cert_number = clean_value(form_data.get("cert_number"))
    staged_card.grade_estimate = clean_value(form_data.get("grade_estimate"))
    staged_card.quantity = int(form_data.get("quantity") or 1)
    staged_card.purchase_price = form_data.get("purchase_price") or None
    staged_card.estimated_value = form_data.get("estimated_value") or None
    staged_card.asking_price = form_data.get("asking_price") or None
    staged_card.purchase_date = purchase_date_value(form_data)
    staged_card.acquisition_source = acquisition_value(form_data.get("acquisition_source"))
    staged_card.acquisition_date = acquisition_date_value(form_data)
    staged_card.acquisition_event = clean_value(form_data.get("acquisition_event"))
    staged_card.storage_location = clean_value(form_data.get("storage_location"))
    staged_card.collection_type = form_data.get("collection_type") or "Inventory"
    staged_card.status = form_data.get("status") or "Active"
    staged_card.notes = form_data.get("notes")


def validate_staged_card_for_import(staged_card):
    """Return missing required inventory-intake fields for a staged card."""
    required_fields = [
        ("player_name", "Player / Subject"),
        ("sport", "Sport"),
        ("card_type", "Card Type"),
        ("collection_type", "Collection Type"),
        ("status", "Status"),
        ("quantity", "Quantity"),
        ("year", "Year"),
        ("brand", "Brand"),
        ("set_name", "Set"),
        ("card_number", "Card Number"),
        ("storage_location", "Storage Location"),
        ("purchase_price", "Cost"),
    ]

    missing_fields = []

    for field_name, label in required_fields:
        value = getattr(staged_card, field_name, None)

        if value is None:
            missing_fields.append(label)
            continue

        if isinstance(value, str) and not value.strip():
            missing_fields.append(label)
            continue

        if field_name == "quantity":
            try:
                if int(value) < 1:
                    missing_fields.append(label)
            except (TypeError, ValueError):
                missing_fields.append(label)

    return missing_fields


def next_staged_review_card(current_staging_id):
    """Return the next non-imported/non-rejected staged card for fast intake."""
    return (
        CardImportStaging.query
        .filter(CardImportStaging.id != current_staging_id)
        .filter(CardImportStaging.ai_status.in_(["Needs Manual Review", "Pending Review"]))
        .order_by(CardImportStaging.created_at.desc(), CardImportStaging.id.desc())
        .first()
    )


@app.route("/ai-import/<int:staging_id>/import", methods=["POST"])
def import_staged_card(staging_id):
    staged_card = CardImportStaging.query.get_or_404(staging_id)

    if staged_card.ai_status == "Imported":
        flash("This staged card has already been imported.")
        return redirect(url_for("ai_import_review"))

    apply_staged_import_form(staged_card, request.form)

    missing_fields = validate_staged_card_for_import(staged_card)

    if missing_fields:
        staged_card.ai_status = "Needs Manual Review"
        db.session.commit()

        flash(
            "Please complete these required fields before importing: "
            + ", ".join(missing_fields)
        )

        return redirect(
            url_for(
                "ai_import_review",
                status=staged_card.ai_status,
                focus=staged_card.id,
                missing=",".join(missing_fields)
            )
        )

    duplicate_action = request.form.get("duplicate_action") or "create_new"
    probable_duplicate = find_probable_duplicate_from_staging(staged_card)
    next_card = next_staged_review_card(staged_card.id)

    if probable_duplicate and duplicate_action == "increase_quantity":
        old_quantity = probable_duplicate.quantity or 1
        probable_duplicate.quantity = old_quantity + (staged_card.quantity or 1)

        if staged_card.image_filename and not probable_duplicate.image_filename:
            probable_duplicate.image_filename = staged_card.image_filename
            staged_card.image_filename = None

        staged_card.imported_card_id = probable_duplicate.id
        staged_card.ai_status = "Imported"
        staged_card.imported_at = datetime.utcnow()

        db.session.commit()

        if next_card:
            flash("Card imported successfully. Loading next card...")
            return redirect(
                url_for(
                    "ai_import_review",
                    status=next_card.ai_status,
                    focus=next_card.id,
                    imported="1"
                )
            )

        flash("Card imported successfully. Review queue is clear.")
        return redirect(url_for("ai_import_review", status="Pending Review", imported="1"))

    new_card = Card(
        card_code=generate_card_code(),
        sport=staged_card.sport or "Baseball",
        player_name=staged_card.player_name,
        year=staged_card.year,
        brand=staged_card.brand,
        set_name=staged_card.set_name,
        card_number=staged_card.card_number,
        variation=staged_card.variation,
        is_rookie=staged_card.is_rookie,
        is_hof=staged_card.is_hof,
        card_type=staged_card.card_type or "Raw",
        grading_company=staged_card.grading_company,
        actual_grade=staged_card.actual_grade,
        cert_number=staged_card.cert_number,
        grade_estimate=staged_card.grade_estimate,
        quantity=staged_card.quantity or 1,
        purchase_price=staged_card.purchase_price,
        estimated_value=staged_card.estimated_value,
        asking_price=staged_card.asking_price,
        purchase_date=staged_card.purchase_date,
        acquisition_source=staged_card.acquisition_source or "Existing Inventory",
        acquisition_date=staged_card.acquisition_date,
        acquisition_event=staged_card.acquisition_event,
        storage_location=staged_card.storage_location,
        collection_type=staged_card.collection_type or "Inventory",
        image_filename=staged_card.image_filename,
        notes=staged_card.notes,
        status=staged_card.status or "Active",
    )

    db.session.add(new_card)
    db.session.flush()

    staged_card.imported_card_id = new_card.id
    staged_card.ai_status = "Imported"
    staged_card.imported_at = datetime.utcnow()
    staged_card.image_filename = None

    db.session.commit()

    if next_card:
        flash("Card imported successfully. Loading next card...")
        return redirect(
            url_for(
                "ai_import_review",
                status=next_card.ai_status,
                focus=next_card.id,
                imported="1"
            )
        )

    flash("Card imported successfully. Review queue is clear.")
    return redirect(url_for("ai_import_review", status="Pending Review", imported="1"))


@app.route("/ai-import/<int:staging_id>/reject", methods=["POST"])
def reject_staged_import(staging_id):
    staged_card = CardImportStaging.query.get_or_404(staging_id)
    staged_card.ai_status = "Rejected"
    db.session.commit()
    flash("AI import rejected.")
    return redirect(request.referrer or url_for("ai_import_review"))


@app.route("/ai-import/<int:staging_id>/delete", methods=["POST"])
def delete_staged_import(staging_id):
    staged_card = CardImportStaging.query.get_or_404(staging_id)
    delete_image_file(staged_card.image_filename)
    db.session.delete(staged_card)
    db.session.commit()
    flash("AI import draft deleted.")
    return redirect(request.referrer or url_for("ai_import_review"))



@app.route("/mobile-capture")
def mobile_capture():
    """Use a phone browser as a CardDesk camera capture station."""
    return render_template("mobile_capture.html")


@app.route("/mobile-capture/upload", methods=["POST"])
def mobile_capture_upload():
    """Receive a captured phone image and save it to AI Review or Image Inbox."""
    uploaded_file = request.files.get("card_image")
    capture_mode = request.form.get("capture_mode") or "ai_review"

    if not uploaded_file or not uploaded_file.filename:
        return {"ok": False, "error": "No image received."}, 400

    image_filename, source_filename = save_uploaded_image_with_source(uploaded_file)

    if not image_filename:
        return {"ok": False, "error": "Image could not be saved."}, 400

    if capture_mode == "image_inbox":
        inbox_image = ImageInbox(
            image_filename=image_filename,
            source_filename=source_filename or uploaded_file.filename,
            source="Mobile Capture",
            status="Available",
            notes="Captured from Mobile Capture."
        )
        db.session.add(inbox_image)
        db.session.commit()

        return {
            "ok": True,
            "mode": "image_inbox",
            "filename": image_filename,
            "image_inbox_id": inbox_image.id,
            "image_url": url_for("uploaded_file", filename=image_filename),
            "image_inbox_url": url_for("image_inbox"),
            "message": "Image saved to Image Inbox."
        }

    staged_card = CardImportStaging(
        image_filename=image_filename,
        source_filename=source_filename or uploaded_file.filename,
        sport=request.form.get("default_sport") or "Baseball",
        collection_type=request.form.get("collection_type") or "Inventory",
        status=request.form.get("status") or "Active",
        purchase_date=purchase_date_value(request.form),
        storage_location=clean_value(request.form.get("storage_location")),
        quantity=1,
        ai_status="Pending Review",
        notes="Captured from Mobile Capture.",
    )

    try:
        raw_response = call_ximilar_for_image(image_filename)
        extracted = extract_card_data_from_ximilar(raw_response)

        staged_card.raw_response_json = json.dumps(raw_response, indent=2, sort_keys=True)
        staged_card.player_name = clean_value(extracted.get("player_name"))
        staged_card.year = extracted.get("year")
        staged_card.sport = extracted.get("sport") or staged_card.sport
        staged_card.brand = clean_value(extracted.get("brand"))
        staged_card.set_name = clean_value(extracted.get("set_name"))
        staged_card.card_number = clean_value(extracted.get("card_number"))
        staged_card.variation = clean_value(extracted.get("variation"))
        staged_card.card_type = extracted.get("card_type") or "Raw"
        staged_card.grading_company = clean_value(extracted.get("grading_company"))
        staged_card.actual_grade = clean_value(extracted.get("actual_grade"))
        staged_card.cert_number = clean_value(extracted.get("cert_number"))
        staged_card.ai_confidence = extracted.get("ai_confidence")
    except Exception as error:
        staged_card.ai_status = "Needs Manual Review"
        staged_card.ai_error = str(error)

    db.session.add(staged_card)
    db.session.commit()

    return {
        "ok": True,
        "mode": "ai_review",
        "filename": image_filename,
        "staging_id": staged_card.id,
        "ai_status": staged_card.ai_status,
        "review_url": url_for("ai_import_review"),
        "message": "Image saved and added to the AI review queue."
    }


@app.route("/image-inbox")
def image_inbox():
    """Browse images captured into CardDesk before they are attached to cards."""
    status_filter = request.args.get("status", "Available")

    query = ImageInbox.query

    if status_filter == "All":
        pass
    elif status_filter == "Used":
        query = query.filter(ImageInbox.status == "Used")
    else:
        status_filter = "Available"
        query = query.filter(ImageInbox.status == "Available")

    images = query.order_by(ImageInbox.created_at.desc(), ImageInbox.id.desc()).all()

    counts = {
        "available": ImageInbox.query.filter(ImageInbox.status == "Available").count(),
        "used": ImageInbox.query.filter(ImageInbox.status == "Used").count(),
        "all": ImageInbox.query.count(),
    }

    return render_template(
        "image_inbox.html",
        images=images,
        status_filter=status_filter,
        counts=counts
    )


@app.route("/image-inbox/<int:image_id>/delete", methods=["POST"])
def delete_image_inbox_item(image_id):
    inbox_image = ImageInbox.query.get_or_404(image_id)

    if inbox_image.status == "Used" or inbox_image.used_card_id:
        flash("Used Image Inbox items are kept so existing card images are not broken.")
        return redirect(request.referrer or url_for("image_inbox"))

    delete_image_file(inbox_image.image_filename)
    db.session.delete(inbox_image)
    db.session.commit()

    flash("Image Inbox item deleted.")
    return redirect(request.referrer or url_for("image_inbox"))


@app.route("/fulfillment")
def fulfillment_queue():
    """Show sold cards that still need post-sale handling."""
    status_filter = request.args.get("status", "")

    query = Card.query.filter(Card.status == "Sold")

    if status_filter:
        query = query.filter(Card.fulfillment_status == status_filter)
    else:
        query = query.filter(
            db.or_(
                Card.fulfillment_status == "Needs Pulling",
                Card.fulfillment_status == "Pulled",
                Card.fulfillment_status == "Ready to Ship",
                Card.fulfillment_status == "Shipped",
                Card.fulfillment_status == "Delivered",
                Card.fulfillment_status == "Completed",
                Card.fulfillment_status.is_(None)
            )
        )

    cards = (
        query
        .order_by(Card.sold_date.desc(), Card.storage_location.asc(), Card.player_name.asc())
        .all()
    )

    counts = {
        "needs_pulling": Card.query.filter(Card.status == "Sold", Card.fulfillment_status == "Needs Pulling").count(),
        "pulled": Card.query.filter(Card.status == "Sold", Card.fulfillment_status == "Pulled").count(),
        "ready_to_ship": Card.query.filter(Card.status == "Sold", Card.fulfillment_status == "Ready to Ship").count(),
        "shipped": Card.query.filter(Card.status == "Sold", Card.fulfillment_status == "Shipped").count(),
        "delivered": Card.query.filter(Card.status == "Sold", Card.fulfillment_status == "Delivered").count(),
        "completed": Card.query.filter(Card.status == "Sold", Card.fulfillment_status == "Completed").count(),
    }

    return render_template(
        "fulfillment.html",
        cards=cards,
        status_filter=status_filter,
        counts=counts
    )


@app.route("/fulfillment/<int:card_id>/status", methods=["POST"])
def update_fulfillment_status(card_id):
    card = Card.query.get_or_404(card_id)

    new_status = request.form.get("fulfillment_status") or "Needs Pulling"
    valid_statuses = ["Needs Pulling", "Pulled", "Ready to Ship", "Shipped", "Delivered", "Completed"]

    if new_status not in valid_statuses:
        flash("Invalid fulfillment status.")
        return redirect(request.referrer or url_for("fulfillment_queue"))

    old_status = card.fulfillment_status or "Needs Pulling"
    card.fulfillment_status = new_status

    if new_status == "Ready to Ship":
        card.shipping_carrier = clean_value(request.form.get("shipping_carrier"))
        card.tracking_number = clean_value(request.form.get("tracking_number"))
        card.shipping_cost = request.form.get("shipping_cost") or None
        card.shipped_date = request.form.get("shipped_date")
        card.shipping_notes = clean_value(request.form.get("shipping_notes"))

    status_note = f"Fulfillment updated from {old_status} to {new_status}."
    if new_status == "Pulled" and card.storage_location:
        status_note += f" Pulled from: {card.storage_location}."

    existing_notes = card.notes or ""
    card.notes = (existing_notes + "\n" if existing_notes else "") + status_note

    db.session.commit()

    flash(f"{card.card_code} fulfillment updated to {new_status}.")

    return redirect(request.referrer or url_for("fulfillment_queue"))


@app.route("/fulfillment/mark-selected-pulled", methods=["POST"])
def mark_selected_fulfillment_pulled():
    card_ids = request.form.getlist("card_ids")

    if not card_ids:
        flash("No cards selected.")
        return redirect(request.referrer or url_for("fulfillment_queue"))

    clean_ids = []

    for card_id in card_ids:
        try:
            clean_ids.append(int(card_id))
        except (TypeError, ValueError):
            continue

    cards = Card.query.filter(Card.id.in_(clean_ids)).all()

    updated_count = 0

    for card in cards:
        old_status = card.fulfillment_status or "Needs Pulling"
        card.fulfillment_status = "Pulled"

        status_note = f"Fulfillment updated from {old_status} to Pulled."
        if card.storage_location:
            status_note += f" Pulled from: {card.storage_location}."

        existing_notes = card.notes or ""
        card.notes = (existing_notes + "\n" if existing_notes else "") + status_note

        updated_count += 1

    db.session.commit()

    flash(f"{updated_count} card(s) marked Pulled.")

    return redirect(request.referrer or url_for("fulfillment_queue"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
