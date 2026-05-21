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

    created_at = db.Column(db.DateTime, server_default=db.func.now())
