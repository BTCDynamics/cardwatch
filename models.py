from flask_sqlalchemy import SQLAlchemy


db = SQLAlchemy()


class Card(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    card_code = db.Column(db.String(50), unique=True, nullable=False)

    player_name = db.Column(db.String(100), nullable=False)

    year = db.Column(db.Integer)

    sport = db.Column(db.String(50))

    brand = db.Column(db.String(100))

    set_name = db.Column(db.String(100))

    card_number = db.Column(db.String(50))

    variation = db.Column(db.String(100))

    is_hof = db.Column(db.Boolean, default=False)

    is_rookie = db.Column(db.Boolean, default=False)

    card_type = db.Column(db.String(20), default="Raw")

    grading_company = db.Column(db.String(50))

    actual_grade = db.Column(db.String(20))

    cert_number = db.Column(db.String(100))

    grade_estimate = db.Column(db.String(20))

    quantity = db.Column(db.Integer, default=1)

    purchase_price = db.Column(db.Float)

    estimated_value = db.Column(db.Float)

    asking_price = db.Column(db.Float)

    sold_price = db.Column(db.Float)

    sold_date = db.Column(db.String(20))

    sales_platform = db.Column(db.String(100))

    purchase_date = db.Column(db.String(20))

    # Acquisition tracking: separates cards already owned from newly acquired inventory
    acquisition_source = db.Column(db.String(50), default="Existing Inventory")

    acquisition_date = db.Column(db.String(20))

    acquisition_event = db.Column(db.String(150))

    storage_location = db.Column(db.String(200))

    image_filename = db.Column(db.String(200))

    notes = db.Column(db.Text)

    status = db.Column(db.String(50), default="Holding")

    collection_type = db.Column(db.String(50), default="Inventory")

    # Deal / transaction tracking
    deal_id = db.Column(db.String(100))

    customer_name = db.Column(db.String(150))

    payment_type = db.Column(db.String(50))

    deal_discount_percent = db.Column(db.Float)

    trade_credit = db.Column(db.Float)

    cash_received = db.Column(db.Float)

    deal_notes = db.Column(db.Text)

    fulfillment_status = db.Column(db.String(50), default="In Storage")

    # Shipping / fulfillment details
    shipping_carrier = db.Column(db.String(50))

    tracking_number = db.Column(db.String(100))

    shipping_cost = db.Column(db.Float)

    shipped_date = db.Column(db.String(20))

    shipping_notes = db.Column(db.Text)

    created_at = db.Column(db.DateTime, server_default=db.func.now())



class CardImportStaging(db.Model):
    """Temporary holding table for AI-recognized cards before they become inventory."""

    id = db.Column(db.Integer, primary_key=True)

    image_filename = db.Column(db.String(200))
    source_filename = db.Column(db.String(255))

    player_name = db.Column(db.String(100))
    year = db.Column(db.Integer)
    sport = db.Column(db.String(50), default="Baseball")
    brand = db.Column(db.String(100))
    set_name = db.Column(db.String(100))
    card_number = db.Column(db.String(50))
    variation = db.Column(db.String(100))

    is_hof = db.Column(db.Boolean, default=False)
    is_rookie = db.Column(db.Boolean, default=False)

    card_type = db.Column(db.String(20), default="Raw")
    grading_company = db.Column(db.String(50))
    actual_grade = db.Column(db.String(20))
    cert_number = db.Column(db.String(100))
    grade_estimate = db.Column(db.String(20))

    quantity = db.Column(db.Integer, default=1)
    purchase_price = db.Column(db.Float)
    estimated_value = db.Column(db.Float)
    asking_price = db.Column(db.Float)
    purchase_date = db.Column(db.String(20))
    acquisition_source = db.Column(db.String(50), default="Existing Inventory")
    acquisition_date = db.Column(db.String(20))
    acquisition_event = db.Column(db.String(150))
    storage_location = db.Column(db.String(200))
    collection_type = db.Column(db.String(50), default="Inventory")
    status = db.Column(db.String(50), default="Active")
    notes = db.Column(db.Text)

    ai_confidence = db.Column(db.Float)
    ai_status = db.Column(db.String(50), default="Pending Review")
    ai_error = db.Column(db.Text)
    raw_response_json = db.Column(db.Text)

    imported_card_id = db.Column(db.Integer, db.ForeignKey("card.id"))
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    imported_at = db.Column(db.DateTime)



class ImageInbox(db.Model):
    """Images captured or uploaded into CardDesk before being attached to a card."""

    id = db.Column(db.Integer, primary_key=True)
    image_filename = db.Column(db.String(200), nullable=False)
    source_filename = db.Column(db.String(255))
    source = db.Column(db.String(50), default="Mobile Capture")
    status = db.Column(db.String(50), default="Available")
    used_card_id = db.Column(db.Integer, db.ForeignKey("card.id"))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    used_at = db.Column(db.DateTime)
