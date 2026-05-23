import os
from datetime import date, datetime, timedelta
from uuid import uuid4

from flask import Flask, render_template, request, redirect, url_for, flash, session
from sqlalchemy import inspect, text
from werkzeug.utils import secure_filename

from models import db, Card

app = Flask(__name__)

app.secret_key = "cardwatch-dev-secret"

app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///cardwatch.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["UPLOAD_FOLDER"] = os.path.join(app.root_path, "static", "uploads")
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024  # 8 MB upload limit

ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

db.init_app(app)


def ensure_upload_folder():
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



with app.app_context():
    db.create_all()
    ensure_database_columns()
    ensure_upload_folder()


def clean_value(value):
    if value:
        return value.strip()
    return None



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
def inject_deal_cart_count():
    return {
        "deal_cart_count": sum(
            (card.quantity or 1)
            for card in get_deal_cart_cards()
        )
    }


def get_deal_cart_quantity():
    """Return total card quantity represented by active deal-cart records."""
    return sum((card.quantity or 1) for card in get_deal_cart_cards())


@app.route("/")
def dashboard():
    today_value = date.today()
    today = today_value.isoformat()

    recent_sales_range = request.args.get("recent_sales_range", "3d")

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
        recent_sales=recent_sales,
        recent_sales_start_date=recent_sales_start_date,
        recent_sales_range=recent_sales_range,
        recent_sales_label=recent_sales_label,
        sales_summary_label=recent_sales_label,
        sales_summary_range=recent_sales_range,
        sales_summary_start_date=recent_sales_start_date,
        deal_cart_count=get_deal_cart_quantity()
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
    min_price = request.args.get("min_price", "")
    max_price = request.args.get("max_price", "")

    query = Card.query

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
                Card.storage_location.ilike(f"%{search_query}%")
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

    if grade_estimate_filter:
        query = query.filter(Card.grade_estimate.ilike(f"%{grade_estimate_filter}%"))

    if actual_grade_filter:
        query = query.filter(Card.actual_grade.ilike(f"%{actual_grade_filter}%"))

    if year_filter:
        query = query.filter(Card.year == int(year_filter))

    if brand_filter:
        query = query.filter(Card.brand.ilike(f"%{brand_filter}%"))

    if storage_filter == "__missing__":
        query = query.filter(db.or_(Card.storage_location.is_(None), Card.storage_location == ""))
    elif storage_filter:
        query = query.filter(Card.storage_location.ilike(f"%{storage_filter}%"))

    if variation_filter:
        query = query.filter(Card.variation.ilike(f"%{variation_filter}%"))

    if min_price:
        query = query.filter(Card.purchase_price >= float(min_price))

    if max_price:
        query = query.filter(Card.purchase_price <= float(max_price))


    if sold_range:
        today_value = date.today()

        if sold_range == "today":
            start_date = today_value
        elif sold_range == "3d":
            start_date = today_value - timedelta(days=2)
        elif sold_range == "7d":
            start_date = today_value - timedelta(days=6)
        elif sold_range == "30d":
            start_date = today_value - timedelta(days=29)
        else:
            start_date = None

        if start_date:
            query = query.filter(Card.sold_date >= start_date.isoformat())

    all_cards = query.order_by(Card.id.desc()).all()

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
        min_price,
        max_price,
    ])

    if has_active_filter:
        summary_cards = all_cards
    else:
        summary_cards = [
            card for card in all_cards
            if card.status == "Sold"
        ]

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
        min_price=min_price,
        max_price=max_price,
        storage_locations=storage_locations,
        deal_cart_ids=deal_cart_ids,
        deal_cart_count=deal_cart_count,
        active_inventory_count=active_inventory_count,
        missing_storage_count=missing_storage_count
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
        card.purchase_date = request.form.get("purchase_date")
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
        clone_source=source_card
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
                purchase_date=request.form.get("purchase_date"),
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
            "purchase_date": request.form.get("purchase_date") or ""
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
                if existing_card.image_filename:
                    delete_image_file(existing_card.image_filename)

                existing_card.image_filename = uploaded_image

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
            purchase_date=request.form.get("purchase_date"),
            storage_location=clean_value(
                request.form.get("storage_location")
            ),
            collection_type=collection_type,
            image_filename=uploaded_image,
            notes=request.form.get("notes"),
            status=card_status
        )

        db.session.add(new_card)
        db.session.commit()

        if force_new_card:
            flash("Cloned card saved as a new inventory record.")
        else:
            flash("New card added successfully.")

        return redirect(url_for("card_detail", card_id=new_card.id))

    return render_template("add_card.html", clone_source=None)


@app.route("/fulfillment")
def fulfillment_queue():
    """Show sold cards that still need post-sale handling."""
    status_filter = request.args.get("status", "Needs Pulling")

    query = Card.query.filter(Card.status == "Sold")

    if status_filter:
        query = query.filter(Card.fulfillment_status == status_filter)
    else:
        query = query.filter(
            db.or_(
                Card.fulfillment_status == "Needs Pulling",
                Card.fulfillment_status == "Pulled",
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
        "shipped": Card.query.filter(Card.status == "Sold", Card.fulfillment_status == "Shipped").count(),
        "delivered": Card.query.filter(Card.status == "Sold", Card.fulfillment_status == "Delivered").count(),
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
    valid_statuses = ["Needs Pulling", "Pulled", "Shipped", "Delivered", "Completed"]

    if new_status not in valid_statuses:
        flash("Invalid fulfillment status.")
        return redirect(request.referrer or url_for("fulfillment_queue"))

    old_status = card.fulfillment_status or "Needs Pulling"
    card.fulfillment_status = new_status

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
    app.run(debug=True)
