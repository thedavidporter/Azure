"""
Generates synthetic data for DM_Hospital_Discharge_01.INPATIENT_ANNUAL_FINAL_2022
Based on DDL from zus1-idoh-dev-v2-sql-dw snapshot.
"""
import csv, random, uuid
from datetime import date, timedelta

random.seed(42)

# ── reference data ─────────────────────────────────────────────────────────────

IN_HOSPITALS = [
    (1001, "1234567890", "IU Health Methodist Hospital",       "46202", "Indianapolis",  "1701 N Senate Blvd"),
    (1002, "2345678901", "Eskenazi Health",                    "46202", "Indianapolis",  "720 Eskenazi Ave"),
    (1003, "3456789012", "Community Hospital East",            "46219", "Indianapolis",  "1500 N Ritter Ave"),
    (1004, "4567890123", "St. Vincent Indianapolis Hospital",  "46260", "Indianapolis",  "2001 W 86th St"),
    (1005, "5678901234", "Parkview Regional Medical Center",   "46845", "Fort Wayne",    "11109 Parkview Plaza Dr"),
    (1006, "6789012345", "Franciscan Health Indianapolis",     "46237", "Indianapolis",  "8111 S Emerson Ave"),
    (1007, "7890123456", "Indiana University Health Ball Memorial", "47303", "Muncie",   "2401 W University Ave"),
    (1008, "8901234567", "Reid Health",                        "47374", "Richmond",      "1100 Reid Pkwy"),
    (1009, "9012345678", "Deaconess Midtown Hospital",         "47714", "Evansville",    "600 Mary St"),
    (1010, "0123456789", "Ascension St. Vincent Evansville",   "47714", "Evansville",    "3700 Washington Ave"),
]

HOSPITAL_ZIPS = {z: (city, addr) for _, _, _, z, city, addr in IN_HOSPITALS}

PATIENT_ZIPS = [
    ("46201","Indianapolis","Marion","18097","Indianapolis"),
    ("46804","Fort Wayne","Allen","18003","Fort Wayne"),
    ("47303","Muncie","Delaware","18035","Muncie"),
    ("47374","Richmond","Wayne","18177","Richmond"),
    ("47711","Evansville","Vanderburgh","18163","Evansville"),
    ("46394","Whiting","Lake","18091","Whiting"),
    ("46901","Kokomo","Howard","18067","Kokomo"),
    ("47601","Boonville","Warrick","18173","Boonville"),
    ("46135","Greencastle","Putnam","18133","Greencastle"),
    ("47130","Jeffersonville","Clark","18019","Jeffersonville"),
]

PTTYPE = [(1,"Inpatient"),(2,"Newborn"),(3,"Outpatient"),(4,"ER Outpatient"),(5,"Swing Bed")]
PRCTYPE = [("IP","Inpatient"),("OP","Outpatient"),("EM","Emergency")]
SERVCODE = [
    (1,"Medical/Surgical"),(2,"Obstetrics"),(3,"Newborn"),(4,"Intensive Care"),
    (5,"Coronary Care"),(6,"Psychiatric"),(7,"Rehabilitation"),(8,"Other"),
]
ADMIT_TYPE = [
    ("1","Emergency"),("2","Urgent"),("3","Elective"),("4","Newborn"),("5","Trauma"),
]
ADMIT_SOURCE = [
    ("1","Physician/Clinic"),("2","Transfer from Hospital"),("4","Transfer from SNF"),
    ("5","Transfer from Another Facility"),("7","Emergency Room"),("9","Other"),
]
DISCHARGE_STATUS = [
    ("01","Discharged to Home"),("02","Discharged to Short-Term Hospital"),
    ("03","Discharged to SNF"),("04","Discharged to ICF"),
    ("05","Discharged to Another Type of Facility"),("06","Discharged to Home Health"),
    ("07","Left AMA"),("20","Expired"),("30","Still a Patient"),("43","Discharged to Federal Hospital"),
]
SEX = [("M","Male"),("F","Female"),("U","Unknown")]
RACE = [
    ("1","White"),("2","Black or African American"),("3","American Indian/Alaska Native"),
    ("4","Asian"),("5","Native Hawaiian/Pacific Islander"),("6","Other"),("9","Unknown"),
]
ETHNICITY = [("1","Not Hispanic or Latino"),("2","Hispanic or Latino"),("9","Unknown")]
LANGUAGE = [("ENG","English"),("SPA","Spanish"),("FRE","French"),("OTH","Other"),("UNK","Unknown")]
GENDER_IDENTITY = [
    ("1","Male"),("2","Female"),("3","Transgender Male"),("4","Transgender Female"),
    ("5","Other"),("9","Choose not to disclose"),
]
PAY_SOURCE = [
    ("MC","Medicare"),("MA","Medicaid"),("BC","Blue Cross"),("CI","Commercial Insurance"),
    ("WC","Workers Compensation"),("SP","Self Pay"),("OT","Other"),
]
PATSEV = [(1,"Minor"),(2,"Moderate"),(3,"Major"),(4,"Extreme")]
RSKMORT = [(1,"Minor"),(2,"Moderate"),(3,"Major"),(4,"Extreme")]
MDC = [str(i) for i in range(1, 26)]
POA = [("Y","Present on Admission"),("N","Not Present on Admission"),("U","Unknown"),("W","Clinically Undetermined")]

# realistic ICD-10-CM codes for inpatient stays
DX_CODES = [
    "I10","I25.10","I50.9","J18.9","N18.3","E11.9","E11.65","Z23","K92.1",
    "S72.001A","I63.9","F32.9","J44.1","N39.0","G89.29","Z87.891","A41.9",
    "K57.30","I48.91","C34.10","I21.9","O80","Z37.0","P07.30","J96.00",
    "M54.5","K29.70","R55","R00.0","T14.91XA","B97.29","U07.1",
]
# ICD-10-PCS procedure codes (simplified)
PX_CODES = [
    "0BH17EZ","5A1935Z","0W110Z9","3E033XZ","3E0F7GC","0D160Z4","0TY00Z0",
    "02100Z8","0GBC0ZZ","0DB60ZZ","04CK0ZZ","0RG00Z0","0QS604Z","0SB30ZZ",
    "F07Z0ZZ","GZ3ZZZZ","HZ2ZZZZ","5A2204Z","3C1ZX8Z","0BDJ8ZX",
]

def rnd_date(start, end):
    delta = (end - start).days
    return start + timedelta(days=random.randint(0, delta))

def rnd_icd10():
    return random.choice(DX_CODES) if random.random() < 0.6 else ""

def rnd_pcs():
    return random.choice(PX_CODES) if random.random() < 0.4 else ""

def rnd_poa():
    code, desc = random.choice(POA)
    return code, desc

# ── build rows ─────────────────────────────────────────────────────────────────

ROWS = 100
rows = []

for i in range(ROWS):
    hosp = random.choice(IN_HOSPITALS)
    hosp_id, npi, hosp_name, hosp_zip, hosp_city, hosp_addr = hosp

    pt_zip_row = random.choice(PATIENT_ZIPS)
    pt_zip, pt_city, pt_county, pt_fips, iha_county = pt_zip_row

    age = random.randint(0, 95)
    dob = rnd_date(date(2022,1,1) - timedelta(days=365*age+180),
                   date(2022,1,1) - timedelta(days=365*age))

    admit_dt = rnd_date(date(2022,1,1), date(2022,12,1))
    los = random.choices([1,2,3,4,5,6,7,10,14,21], weights=[20,20,15,12,10,8,6,4,3,2])[0]
    discharge_dt = admit_dt + timedelta(days=los)

    pttype_code, pttype_desc = random.choice(PTTYPE)
    prctype_code, prctype_desc = random.choice(PRCTYPE)
    serv_code, serv_desc = random.choice(SERVCODE)
    admit_type_code, admit_type_desc = random.choice(ADMIT_TYPE)
    admit_src_code, admit_src_desc = random.choice(ADMIT_SOURCE)
    rpt_admit_src = admit_src_code
    dc_code, dc_desc = random.choice(DISCHARGE_STATUS)
    sex_code, sex_desc = random.choice(SEX)
    race_code, race_desc = random.choice(RACE)
    eth_code, eth_desc = random.choice(ETHNICITY)
    lang_code, lang_desc = random.choice(LANGUAGE)
    gi_code, gi_desc = random.choice(GENDER_IDENTITY)
    pay1_code, pay1_desc = random.choice(PAY_SOURCE)
    pay2_code, pay2_desc = random.choice(PAY_SOURCE)
    pay3_code, pay3_desc = ("", "")
    patsev_code, patsev_desc = random.choice(PATSEV)
    rskmort_code, rskmort_desc = random.choice(RSKMORT)

    dx1 = random.choice(DX_CODES)
    dx1_poa_code, dx1_poa_desc = rnd_poa()
    admit_dx = random.choice(DX_CODES)

    total_charges = round(random.uniform(3000, 180000), 2)
    bw = round(random.uniform(2500, 4200), 0) if pttype_code == 2 else ""

    proc1 = rnd_pcs()
    proc1_date = admit_dt + timedelta(days=random.randint(0, max(los-1,0))) if proc1 else ""

    msdrg = random.randint(1, 999)
    mspline = random.randint(1, 25)
    mdc = random.choice(MDC)
    apr_drg = random.randint(1, 999)

    ccu = random.choice([0, 1])
    ccu_desc = "Yes" if ccu else "No"
    ccu_days = round(random.uniform(0.5, 5.0), 1) if ccu else 0
    emerg = random.choice([0, 1])

    # sparse secondary diagnoses
    def dx_cols(n):
        return [random.choice(DX_CODES) if random.random() < max(0, 0.5 - i*0.04) else "" for i in range(n)]

    def proc_cols(n):
        return [rnd_pcs() if random.random() < max(0, 0.3 - i*0.05) else "" for i in range(n)]

    def proc_date_cols(n, base, max_los):
        return [str(base + timedelta(days=random.randint(0, max(max_los-1,0)))) if random.random() < max(0, 0.2 - i*0.04) else "" for i in range(n)]

    dx_extra = dx_cols(59)
    proc_extra = proc_cols(59)
    proc_dates = proc_date_cols(59, admit_dt, los)

    row = {
        "YEAR": 2022,
        "ID": str(uuid.uuid4()).replace("-","")[:12].upper(),
        "LINKID": str(uuid.uuid4()).replace("-","")[:10].upper(),
        "PATPROXY": "",
        "PCONTROL": f"PC{i+1:07d}",
        "PTTYPE": pttype_code,
        "PTTYPE_DESCRIPTION": pttype_desc,
        "PRCTYPE": prctype_code,
        "PRCTYPE_DESCRIPTION": prctype_desc,
        "SERVCODE": serv_code,
        "SERVCODE_DESCRIPTION": serv_desc,
        "ADMIT_TYPE": admit_type_code,
        "ADMIT_TYPE_DESCRIPTION": admit_type_desc,
        "ADMIT_SOURCE": admit_src_code,
        "ADMIT_SOURCE_DESCRIPTION": admit_src_desc,
        "RPT_ADMIT_SOURCE": rpt_admit_src,
        "DISCHARGE_STATUS": dc_code,
        "DISCHARGE_STATUS_DESCRIPTION": dc_desc,
        "RPT_DISCHARGE_STATUS": dc_code,
        "HOSPITAL_ID": hosp_id,
        "FACILITY_NPI": npi,
        "HOSPITAL_NAME": hosp_name,
        "HOSPITAL_ZIP_CODE": hosp_zip,
        "HOSPITAL_CITY": hosp_city,
        "HOSPITAL_ADDRESS": hosp_addr,
        "GLOBALPATIENTID": random.randint(10000000, 99999999),
        "PATIENT_VISIT_ID": f"VIS{i+1:09d}",
        "PATIENT_VISIT_ID_SEQNUM": 1,
        "BIRTH_DATE": str(dob),
        "AGE": age,
        "AGE_DAYS": age * 365 + random.randint(0, 364),
        "CALC_AGE": age,
        "SEX": sex_code,
        "SEX_DESCRIPTION": sex_desc,
        "RACE": race_code,
        "RACE_DESCRIPTION": race_desc,
        "ETHNICITY": eth_code,
        "ETHNICITY_DESCRIPTION": eth_desc,
        "LANGUAGE": lang_code,
        "LANGUAGE_DESCRIPTION": lang_desc,
        "GENDER_IDENTITY": gi_code,
        "GENDER_IDENTITY_DESCRIPTION": gi_desc,
        "BIRTHWEIGHT_VALUE": bw,
        "STATE": "IN",
        "ZIP_CODE": pt_zip,
        "COUNTY_FIPS": pt_fips,
        "COUNTY_NAME": pt_county,
        "COUNTY": pt_county,
        "IHA_COUNTY_ID": iha_county,
        "CITY": pt_city,
        "TOTAL_CHARGES": total_charges,
        "PAY_SOURCE_1": pay1_code,
        "PAY_SOURCE_1_DESCRIPTION": pay1_desc,
        "PAY_SOURCE_2": pay2_code,
        "PAY_SOURCE_2_DESCRIPTION": pay2_desc,
        "PAY_SOURCE_3": pay3_code,
        "PAY_SOURCE_3_DESCRIPTION": pay3_desc,
        "ADMIT_DATE": str(admit_dt),
        "DISCHARGE_DATE": str(discharge_dt),
        "CALC_LOS": los,
        "IHA_LOS": los,
        "ADMITTING_DIAGNOSIS": admit_dx,
        "DIAGNOSIS_1": dx1,
        "DXP_POA": dx1_poa_code,
        "DXP_POA_DESCRIPTION": dx1_poa_desc,
        "PATSEV": patsev_code,
        "PATSEV_DESCRIPTION": patsev_desc,
        "RSKMORT": rskmort_code,
        "RSKMORT_DESCRIPTION": rskmort_desc,
        "MSDRG": msdrg,
        "MSPLINE": mspline,
        "MDC": mdc,
        "APR_DRG": apr_drg,
        "DXE1": dx_extra[0],  "DXE1_POA": rnd_poa()[0] if dx_extra[0] else "",
        "DXE2": dx_extra[1],  "DXE2_POA": rnd_poa()[0] if dx_extra[1] else "",
        "DXE3": dx_extra[2],  "DXE3_POA": rnd_poa()[0] if dx_extra[2] else "",
        "PROCEDURE_1": proc1,
        "PRINCIPAL_PROCEDURE_DATE": str(proc1_date) if proc1_date else "",
        "CRITICAL_CARE_UNIT": ccu,
        "CRITICAL_CARE_UNIT_DESCRIPTION": ccu_desc,
        "CRITICAL_CARE_DAYS": ccu_days,
        "EMERGENCYSERVICES": emerg,
        "SM_ABS": random.randint(0,1),
        "SM_LR": random.randint(0,1),
        "SM_OBS": random.randint(0,1),
        "SM_TH": random.randint(0,1),
        "SM_RAD": random.randint(0,1),
        "SM_OTH": random.randint(0,1),
        "PSYCH_REHAB": random.choice([0,1]),
        "PSYCH_REHAB_DESCRIPTION": "",
        "PINA": random.randint(100000,999999),
        "PINB": random.randint(100000,999999),
        "PINC": random.randint(100000,999999),
        "PIND": random.randint(100000,999999),
        "PINE": random.randint(100000,999999),
        "REVCODE": "",
        "UNITSERV": "",
        "OPSERVICELINE": "",
        "OPSERVICELINETIER": "",
        "SERVICELINECODE": "",
        "SERVICELINECODETYPE": "",
        "NOTE": "",
        "NOTE1": "",
        "ACE_TIMESTAMP": f"2023-01-15 00:00:00.0000000",
    }

    # DIAGNOSIS_2 – DIAGNOSIS_60
    for n in range(2, 61):
        row[f"DIAGNOSIS_{n}"] = dx_extra[n-2]

    # PROCEDURE_2 – PROCEDURE_60
    for n in range(2, 61):
        row[f"PROCEDURE_{n}"] = proc_extra[n-2]

    # PROC_2_DATE – PROC_60_DATE
    for n in range(2, 61):
        row[f"PROC_{n}_DATE"] = proc_dates[n-2]

    rows.append(row)

# ── write CSV in column order ──────────────────────────────────────────────────

COLUMNS = [
    "YEAR","ID","LINKID","PATPROXY","PCONTROL","PTTYPE","PTTYPE_DESCRIPTION",
    "PRCTYPE","PRCTYPE_DESCRIPTION","SERVCODE","SERVCODE_DESCRIPTION",
    "ADMIT_TYPE","ADMIT_TYPE_DESCRIPTION","ADMIT_SOURCE","ADMIT_SOURCE_DESCRIPTION",
    "RPT_ADMIT_SOURCE","DISCHARGE_STATUS","DISCHARGE_STATUS_DESCRIPTION",
    "RPT_DISCHARGE_STATUS","HOSPITAL_ID","FACILITY_NPI","HOSPITAL_NAME",
    "HOSPITAL_ZIP_CODE","HOSPITAL_CITY","HOSPITAL_ADDRESS","GLOBALPATIENTID",
    "PATIENT_VISIT_ID","PATIENT_VISIT_ID_SEQNUM","BIRTH_DATE","AGE","AGE_DAYS",
    "CALC_AGE","SEX","SEX_DESCRIPTION","RACE","RACE_DESCRIPTION","ETHNICITY",
    "ETHNICITY_DESCRIPTION","LANGUAGE","LANGUAGE_DESCRIPTION","GENDER_IDENTITY",
    "GENDER_IDENTITY_DESCRIPTION","BIRTHWEIGHT_VALUE","STATE","ZIP_CODE",
    "COUNTY_FIPS","COUNTY_NAME","COUNTY","IHA_COUNTY_ID","CITY","TOTAL_CHARGES",
    "PAY_SOURCE_1","PAY_SOURCE_1_DESCRIPTION","PAY_SOURCE_2","PAY_SOURCE_2_DESCRIPTION",
    "PAY_SOURCE_3","PAY_SOURCE_3_DESCRIPTION","ADMIT_DATE","DISCHARGE_DATE",
    "CALC_LOS","IHA_LOS","ADMITTING_DIAGNOSIS","DIAGNOSIS_1","DXP_POA",
    "DXP_POA_DESCRIPTION","PATSEV","PATSEV_DESCRIPTION","RSKMORT","RSKMORT_DESCRIPTION",
    "MSDRG","MSPLINE","MDC","APR_DRG",
    "DXE1","DXE1_POA","DXE2","DXE2_POA","DXE3","DXE3_POA",
    "PROCEDURE_1","PRINCIPAL_PROCEDURE_DATE",
    "CRITICAL_CARE_UNIT","CRITICAL_CARE_UNIT_DESCRIPTION","CRITICAL_CARE_DAYS",
    "EMERGENCYSERVICES","SM_ABS","SM_LR","SM_OBS","SM_TH","SM_RAD","SM_OTH",
    "PSYCH_REHAB","PSYCH_REHAB_DESCRIPTION",
    "PINA","PINB","PINC","PIND","PINE",
    "REVCODE","UNITSERV","OPSERVICELINE","OPSERVICELINETIER",
    "SERVICELINECODE","SERVICELINECODETYPE","NOTE","NOTE1",
] + [f"DIAGNOSIS_{n}" for n in range(2, 61)] \
  + [f"PROCEDURE_{n}" for n in range(2, 61)] \
  + [f"PROC_{n}_DATE" for n in range(2, 61)] \
  + ["ACE_TIMESTAMP"]

out = "/home/thedavidporter/INPATIENT_ANNUAL_FINAL_2022_synthetic.csv"
with open(out, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=COLUMNS)
    writer.writeheader()
    writer.writerows(rows)

print(f"Written {ROWS} rows × {len(COLUMNS)} columns → {out}")
