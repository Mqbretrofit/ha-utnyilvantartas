DOMAIN = "utnyilvantartas"
PLATFORMS = ["sensor", "binary_sensor", "button"]

CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_DEVICE_ID = "device_id"
CONF_VEHICLE_NAME = "vehicle_name"
CONF_HOME_ZONE = "home_zone"
CONF_WORK_ZONE = "work_zone"
CONF_KELIO_ENTITY = "kelio_entity"
CONF_KELIO_MONTH_ENTITY = "kelio_month_entity"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_HOME_GPS_RADIUS = "home_gps_radius_m"
CONF_WORK_GPS_RADIUS = "work_gps_radius_m"
CONF_ENDPOINT_WINDOW_KM = "endpoint_window_km"

DEFAULT_HOME_ZONE = "zone.home"
DEFAULT_WORK_ZONE = "zone.munkahely"
DEFAULT_SCAN_INTERVAL = 15
DEFAULT_HOME_GPS_RADIUS = 300
DEFAULT_WORK_GPS_RADIUS = 600
DEFAULT_ENDPOINT_WINDOW_KM = 1.0
DEFAULT_KELIO_ENTITY = "binary_sensor.kelio_jelenlet_ma"
DEFAULT_KELIO_MONTH_ENTITY = "sensor.kelio_havi_jelenlet"

BASE_URL = "https://service.alapnyomkovetes.hu"
LOGIN_URL = f"{BASE_URL}/login"
PAST_URL = f"{BASE_URL}/positions/ajaxGetDeviceLocationList/past"
ROUTE_LINES_URL = f"{BASE_URL}/statistics/ajaxGetLocationLines"
ROUTE_GRID_URL = f"{BASE_URL}/statistics/ajaxGetStatDeviceGrid"

ATTR_REASON = "reason"
ATTR_FIRST_POINT = "first_point"
ATTR_LAST_POINT = "last_point"
ATTR_FIRST_TIME = "first_time"
ATTR_LAST_TIME = "last_time"
ATTR_TOTAL_RECORDS = "total_records"
ATTR_VALID_POINTS = "valid_points"
ATTR_UNIQUE_POINTS = "unique_points"

KELIO_ADDON_SLUG = "local_kelio_presence"

# PDF riport beállítások
CONF_REPORT_COMPANY_NAME = "report_company_name"
CONF_REPORT_COMPANY_ADDRESS = "report_company_address"
CONF_REPORT_TAX_NUMBER = "report_tax_number"
CONF_REPORT_EMPLOYEE_NAME = "report_employee_name"
CONF_REPORT_PRIVATE_VEHICLE = "report_private_vehicle"  # rendszám
CONF_REPORT_VEHICLE_TYPE = "report_vehicle_type"
CONF_REPORT_FUEL_TYPE = "report_fuel_type"
CONF_REPORT_FUEL_CONSUMPTION = "report_fuel_consumption_l_100km"
CONF_REPORT_ENGINE_CC = "report_engine_cc"
CONF_REPORT_START_ODOMETER = "report_start_odometer_km"
CONF_REPORT_HOME_LABEL = "report_home_label"
CONF_REPORT_HOME_ADDRESS = "report_home_address"
CONF_REPORT_FUEL_PRICE = "report_fuel_price_huf_l"
CONF_REPORT_TRIP_NATURE = "report_trip_nature"
CONF_COMMUTE_ONE_WAY_KM = "commute_one_way_km"
CONF_REIMBURSEMENT_HUF_PER_KM = "reimbursement_huf_per_km"

CONF_EMAIL_RECIPIENT = "email_recipient"
CONF_EMAIL_SUBJECT = "email_subject"
CONF_EMAIL_BODY = "email_body"

DEFAULT_REPORT_COMPANY_NAME = ""
DEFAULT_REPORT_COMPANY_ADDRESS = ""
DEFAULT_REPORT_TAX_NUMBER = ""
DEFAULT_REPORT_EMPLOYEE_NAME = ""
DEFAULT_REPORT_PRIVATE_VEHICLE = ""
DEFAULT_REPORT_VEHICLE_TYPE = ""
DEFAULT_REPORT_FUEL_TYPE = "Benzin"
DEFAULT_REPORT_FUEL_CONSUMPTION = 0.0
DEFAULT_REPORT_ENGINE_CC = 0
DEFAULT_REPORT_START_ODOMETER = 0.0
DEFAULT_REPORT_HOME_LABEL = "Lakás"
DEFAULT_REPORT_HOME_ADDRESS = ""
DEFAULT_REPORT_FUEL_PRICE = 0.0
DEFAULT_REPORT_TRIP_NATURE = "csak magán utak"
DEFAULT_COMMUTE_ONE_WAY_KM = 0.0
DEFAULT_REIMBURSEMENT_HUF_PER_KM = 30.0

DEFAULT_EMAIL_RECIPIENT = ""
DEFAULT_EMAIL_SUBJECT = "Útnyilvántartás – {month}"
DEFAULT_EMAIL_BODY = """Tisztelt Címzett!

Csatolva küldöm a {month} havi útnyilvántartást PDF formátumban.

Üdvözlettel:
{employee}"""
