"""Distances between areas, with no paid map service: approximate centre points of Hyderabad / Secunderabad areas
(and a few other Telangana / AP cities) built into TeamOS. Good to about 1 km, which is what "within 5 km of
Kondapur" needs. Unknown names simply have no distance (nothing breaks); new areas can be added to PLACES.
"""
from __future__ import annotations

import math
import re

# name: (latitude, longitude) — area centre points, approximate
PLACES: dict[str, tuple[float, float]] = {
    # West / IT corridor
    "kondapur": (17.4600, 78.3640), "gachibowli": (17.4401, 78.3489), "madhapur": (17.4483, 78.3915),
    "hitec city": (17.4475, 78.3765), "kukatpally": (17.4849, 78.4138), "miyapur": (17.4968, 78.3614),
    "chandanagar": (17.4931, 78.3290), "lingampally": (17.4834, 78.3171), "nallagandla": (17.4718, 78.3093),
    "manikonda": (17.4040, 78.3860), "narsingi": (17.3910, 78.3570), "kokapet": (17.3940, 78.3330),
    "financial district": (17.4140, 78.3420), "nanakramguda": (17.4180, 78.3460), "tellapur": (17.4600, 78.2900),
    "bachupally": (17.5440, 78.3650), "nizampet": (17.5150, 78.3850), "pragathi nagar": (17.5240, 78.3990),
    "kphb": (17.4930, 78.3990), "jntu": (17.4930, 78.3910), "hafeezpet": (17.4800, 78.3540),
    "kothaguda": (17.4580, 78.3700), "raidurg": (17.4280, 78.3810), "puppalaguda": (17.3900, 78.3780),
    "patancheru": (17.5320, 78.2650), "beeramguda": (17.5130, 78.2960), "ramachandrapuram": (17.5000, 78.3000),
    "gopanpally": (17.4500, 78.3150), "serilingampally": (17.4620, 78.3290), "moosapet": (17.4700, 78.4250),
    "balanagar": (17.4740, 78.4460), "jagadgirigutta": (17.5160, 78.4200), "quthbullapur": (17.5000, 78.4500),
    "kompally": (17.5380, 78.4870), "suchitra": (17.5040, 78.4820), "gajularamaram": (17.5300, 78.4400),
    "dundigal": (17.5900, 78.4200), "medchal": (17.6290, 78.4810), "bowenpally": (17.4730, 78.4770),
    # Central
    "banjara hills": (17.4156, 78.4347), "jubilee hills": (17.4326, 78.4071), "film nagar": (17.4140, 78.4080),
    "ameerpet": (17.4375, 78.4482), "sr nagar": (17.4410, 78.4410), "punjagutta": (17.4260, 78.4500),
    "somajiguda": (17.4240, 78.4580), "begumpet": (17.4430, 78.4660), "yousufguda": (17.4370, 78.4280),
    "erragadda": (17.4560, 78.4330), "borabanda": (17.4500, 78.4150), "sanath nagar": (17.4560, 78.4430),
    "khairatabad": (17.4150, 78.4630), "lakdikapul": (17.4040, 78.4650), "himayatnagar": (17.4010, 78.4870),
    "abids": (17.3920, 78.4760), "koti": (17.3850, 78.4860), "nampally": (17.3890, 78.4680),
    "narayanguda": (17.3960, 78.4930), "masab tank": (17.4000, 78.4530), "mehdipatnam": (17.3940, 78.4400),
    "tolichowki": (17.3960, 78.4140), "attapur": (17.3720, 78.4280), "rajendra nagar": (17.3220, 78.4040),
    "gandipet": (17.3800, 78.3000), "langar houz": (17.3790, 78.4290), "golconda": (17.3830, 78.4010),
    "shaikpet": (17.4060, 78.3940),
    # North / Secunderabad
    "secunderabad": (17.4399, 78.4983), "paradise": (17.4430, 78.4870), "trimulgherry": (17.4730, 78.5050),
    "alwal": (17.5010, 78.5050), "malkajgiri": (17.4530, 78.5280), "sainikpuri": (17.4910, 78.5520),
    "as rao nagar": (17.4790, 78.5600), "ecil": (17.4720, 78.5700), "kapra": (17.4870, 78.5640),
    "neredmet": (17.4790, 78.5350), "yapral": (17.5030, 78.5350), "tarnaka": (17.4270, 78.5390),
    "habsiguda": (17.4180, 78.5450), "uppal": (17.4050, 78.5590), "nacharam": (17.4300, 78.5600),
    "marredpally": (17.4500, 78.5100), "sitaphalmandi": (17.4280, 78.5100), "bolarum": (17.5260, 78.5110),
    "kowkur": (17.5180, 78.5480), "boduppal": (17.4140, 78.5780), "peerzadiguda": (17.3990, 78.5870),
    "ghatkesar": (17.4500, 78.6850), "medipally": (17.4080, 78.5970),
    # East / South-east
    "lb nagar": (17.3457, 78.5522), "dilsukhnagar": (17.3688, 78.5247), "kothapet": (17.3680, 78.5410),
    "chaitanyapuri": (17.3700, 78.5360), "vanasthalipuram": (17.3290, 78.5660), "hayathnagar": (17.3270, 78.6040),
    "nagole": (17.3720, 78.5600), "malakpet": (17.3740, 78.5050), "saroornagar": (17.3530, 78.5320),
    "karmanghat": (17.3420, 78.5420), "champapet": (17.3420, 78.5180), "santoshnagar": (17.3450, 78.5020),
    "amberpet": (17.3920, 78.5170), "ramanthapur": (17.3980, 78.5350), "vidyanagar": (17.4040, 78.5070),
    "kachiguda": (17.3880, 78.4990), "musheerabad": (17.4120, 78.4990), "chikkadpally": (17.4060, 78.4970),
    "ramnagar": (17.4120, 78.5110), "adikmet": (17.4120, 78.5200), "bn reddy nagar": (17.3220, 78.5550),
    "meerpet": (17.3220, 78.5250), "badangpet": (17.3060, 78.5250), "turkayamjal": (17.2980, 78.5940),
    "pedda amberpet": (17.3300, 78.6200), "abdullapurmet": (17.3170, 78.6550), "ibrahimpatnam": (17.1920, 78.6500),
    # South / Old city
    "charminar": (17.3616, 78.4747), "falaknuma": (17.3310, 78.4670), "chandrayangutta": (17.3150, 78.4880),
    "bahadurpura": (17.3530, 78.4520), "kishanbagh": (17.3500, 78.4370), "aramghar": (17.3180, 78.4300),
    "shamshabad": (17.2530, 78.4250), "mailardevpally": (17.3270, 78.4400), "katedan": (17.3100, 78.4450),
    "jalpally": (17.2950, 78.4870), "balapur": (17.2950, 78.5050), "barkas": (17.3150, 78.4950),
    "yakutpura": (17.3570, 78.4990), "dabeerpura": (17.3640, 78.4850), "malakpet colony": (17.3740, 78.5050),
    # Other cities (city centre)
    "hyderabad": (17.3850, 78.4867), "vizag": (17.6868, 83.2185), "visakhapatnam": (17.6868, 83.2185),
    "vijayawada": (16.5062, 80.6480), "guntur": (16.3067, 80.4365), "warangal": (17.9689, 79.5941),
    "karimnagar": (18.4386, 79.1288), "nizamabad": (18.6725, 78.0941), "khammam": (17.2473, 80.1514),
    "nellore": (14.4426, 79.9865), "tirupati": (13.6288, 79.4192), "kurnool": (15.8281, 78.0373),
    "rajahmundry": (17.0005, 81.8040), "kakinada": (16.9891, 82.2475), "mahbubnagar": (16.7488, 77.9850),
    "nalgonda": (17.0575, 79.2671), "siddipet": (18.1018, 78.8520), "sangareddy": (17.6140, 78.0816),
    "bangalore": (12.9716, 77.5946), "bengaluru": (12.9716, 77.5946), "chennai": (13.0827, 80.2707),
    # More South India — city centres
    "anantapur": (14.6819, 77.6006), "kadapa": (14.4674, 78.8241), "ongole": (15.5057, 80.0499),
    "eluru": (16.7107, 81.0952), "bhimavaram": (16.5449, 81.5212), "srikakulam": (18.2949, 83.8938),
    "vizianagaram": (18.1067, 83.3956), "machilipatnam": (16.1875, 81.1389), "chittoor": (13.2172, 79.1003),
    "adilabad": (19.6641, 78.5320), "mancherial": (18.8714, 79.4443), "ramagundam": (18.7550, 79.4740),
    "suryapet": (17.1405, 79.6236), "miryalaguda": (16.8722, 79.5625), "kothagudem": (17.5500, 80.6200),
    "mysore": (12.2958, 76.6394), "mysuru": (12.2958, 76.6394), "mangalore": (12.9141, 74.8560),
    "mangaluru": (12.9141, 74.8560), "hubli": (15.3647, 75.1240), "dharwad": (15.4589, 75.0078),
    "belgaum": (15.8497, 74.4977), "belagavi": (15.8497, 74.4977), "davangere": (14.4644, 75.9218),
    "shimoga": (13.9299, 75.5681), "tumkur": (13.3409, 77.1010), "udupi": (13.3409, 74.7421),
    "kalaburagi": (17.3297, 76.8343), "gulbarga": (17.3297, 76.8343), "ballari": (15.1394, 76.9214),
    "coimbatore": (11.0168, 76.9558), "madurai": (9.9252, 78.1198), "trichy": (10.7905, 78.7047),
    "tiruchirappalli": (10.7905, 78.7047), "salem": (11.6643, 78.1460), "tirunelveli": (8.7139, 77.7567),
    "erode": (11.3410, 77.7172), "vellore": (12.9165, 79.1325), "tiruppur": (11.1085, 77.3411),
    "thanjavur": (10.7870, 79.1378), "hosur": (12.7409, 77.8253), "kanchipuram": (12.8342, 79.7036),
    "puducherry": (11.9416, 79.8083), "pondicherry": (11.9416, 79.8083), "ooty": (11.4102, 76.6950),
    "kochi": (9.9312, 76.2673), "cochin": (9.9312, 76.2673), "ernakulam": (9.9816, 76.2999),
    "thiruvananthapuram": (8.5241, 76.9366), "trivandrum": (8.5241, 76.9366), "kozhikode": (11.2588, 75.7804),
    "calicut": (11.2588, 75.7804), "thrissur": (10.5276, 76.2144), "kollam": (8.8932, 76.6141),
    "kannur": (11.8745, 75.3704), "kottayam": (9.5916, 76.5222), "palakkad": (10.7867, 76.6548),
    "alappuzha": (9.4981, 76.3388),
    # Bengaluru areas
    "koramangala": (12.9352, 77.6245), "indiranagar": (12.9784, 77.6408), "whitefield": (12.9698, 77.7500),
    "hsr layout": (12.9116, 77.6474), "btm layout": (12.9166, 77.6101), "jayanagar": (12.9250, 77.5938),
    "jp nagar": (12.9063, 77.5857), "electronic city": (12.8452, 77.6602), "marathahalli": (12.9591, 77.6974),
    "hebbal": (13.0358, 77.5970), "yelahanka": (13.1007, 77.5963), "malleshwaram": (13.0035, 77.5710),
    "rajajinagar": (12.9915, 77.5543), "basavanagudi": (12.9416, 77.5750), "banashankari": (12.9255, 77.5468),
    "bellandur": (12.9257, 77.6764), "sarjapur": (12.8600, 77.7860), "hennur": (13.0359, 77.6413),
    "kr puram": (13.0076, 77.6950), "mg road": (12.9756, 77.6050), "rt nagar": (13.0213, 77.5950),
    "vijayanagar": (12.9719, 77.5300), "kengeri": (12.9081, 77.4851), "bannerghatta road": (12.8880, 77.5970),
    "hsr": (12.9116, 77.6474), "sahakara nagar": (13.0622, 77.5867),
    # Chennai areas
    "t nagar": (13.0418, 80.2341), "anna nagar": (13.0850, 80.2101), "velachery": (12.9815, 80.2180),
    "adyar": (13.0012, 80.2565), "tambaram": (12.9249, 80.1000), "porur": (13.0382, 80.1565),
    "omr": (12.9010, 80.2279), "sholinganallur": (12.9010, 80.2279), "mylapore": (13.0368, 80.2676),
    "nungambakkam": (13.0569, 80.2425), "kodambakkam": (13.0521, 80.2255), "vadapalani": (13.0500, 80.2121),
    "ashok nagar": (13.0370, 80.2120), "chromepet": (12.9516, 80.1462), "guindy": (13.0067, 80.2206),
    "besant nagar": (13.0002, 80.2668), "perungudi": (12.9654, 80.2461), "medavakkam": (12.9200, 80.1920),
    "pallavaram": (12.9675, 80.1491), "ambattur": (13.1143, 80.1548), "kilpauk": (13.0827, 80.2400),
    "royapettah": (13.0540, 80.2640), "ecr": (12.8996, 80.2530), "valasaravakkam": (13.0400, 80.1720),
    # Visakhapatnam areas
    "mvp colony": (17.7406, 83.3367), "dwaraka nagar": (17.7290, 83.3080), "gajuwaka": (17.6868, 83.2070),
    "madhurawada": (17.8100, 83.3700), "seethammadhara": (17.7400, 83.3100), "rushikonda": (17.7820, 83.3850),
    "maddilapalem": (17.7350, 83.3200), "pm palem": (17.8000, 83.3500), "akkayapalem": (17.7290, 83.2980),
    "kancharapalem": (17.7330, 83.2760), "pendurthi": (17.8000, 83.2000), "steel plant": (17.6100, 83.1900),
    # Vijayawada areas
    "benz circle": (16.5000, 80.6550), "labbipet": (16.5050, 80.6400), "governorpet": (16.5150, 80.6300),
    "patamata": (16.4950, 80.6700), "gunadala": (16.5250, 80.6650), "moghalrajpuram": (16.5100, 80.6480),
    "auto nagar vijayawada": (16.4950, 80.6900), "kanuru": (16.4870, 80.6900), "poranki": (16.4750, 80.7100),
    # Kochi & Coimbatore areas
    "kakkanad": (10.0159, 76.3419), "edappally": (10.0261, 76.3125), "vyttila": (9.9667, 76.3200),
    "kaloor": (9.9970, 76.2990), "fort kochi": (9.9658, 76.2421), "palarivattom": (10.0050, 76.3070),
    "rs puram": (11.0100, 76.9500), "gandhipuram": (11.0168, 76.9660), "saibaba colony": (11.0250, 76.9450),
    "peelamedu": (11.0300, 77.0150), "race course coimbatore": (11.0000, 76.9750),
}

ALIASES = {
    "hitech city": "hitec city", "hi-tech city": "hitec city", "hitex": "hitec city", "cyberabad": "hitec city",
    "kphb colony": "kphb", "kukatpally housing board": "kphb", "jntu kukatpally": "jntu",
    "s r nagar": "sr nagar", "s.r. nagar": "sr nagar", "s.r nagar": "sr nagar", "sanathnagar": "sanath nagar",
    "l b nagar": "lb nagar", "l.b. nagar": "lb nagar", "l.b nagar": "lb nagar", "dilsukh nagar": "dilsukhnagar",
    "banjarahills": "banjara hills", "jubileehills": "jubilee hills", "jubilee hill": "jubilee hills",
    "banjara hill": "banjara hills", "a s rao nagar": "as rao nagar", "a.s. rao nagar": "as rao nagar",
    "e c i l": "ecil", "rajendranagar": "rajendra nagar", "tolichowk": "tolichowki", "toli chowki": "tolichowki",
    "manikonda jagir": "manikonda", "nizampet road": "nizampet", "pragati nagar": "pragathi nagar",
    "gachibowli financial district": "financial district", "nanakramguda financial district": "nanakramguda",
    "secundrabad": "secunderabad", "secbad": "secunderabad", "hyd": "hyderabad", "hyderbad": "hyderabad",
    "vishakapatnam": "visakhapatnam", "visakapatnam": "visakhapatnam", "kondapur hyderabad": "kondapur",
    "miyapur hyderabad": "miyapur", "kothapeta": "kothapet", "vanasthali puram": "vanasthalipuram",
    "hayath nagar": "hayathnagar", "b n reddy": "bn reddy nagar", "bn reddy": "bn reddy nagar",
    "uppal x roads": "uppal", "uppal depot": "uppal", "medchal road": "medchal", "kompalli": "kompally",
    "suchitra circle": "suchitra", "alwal hyderabad": "alwal", "ameerpet hyderabad": "ameerpet",
    "madhapur hyderabad": "madhapur", "begum pet": "begumpet", "punjagutta hyderabad": "punjagutta",
    "panjagutta": "punjagutta", "himayath nagar": "himayatnagar", "narayanaguda": "narayanguda",
}

DEFAULT_RADIUS_KM = 5  # when the buyer has not said how far is OK

RADIUS_CHOICES = [(1, "Within 1–2 km"), (3, "Within 3 km"), (5, "Within 5 km"), (10, "Within 10 km"),
                  (20, "Anywhere in the city")]


def _norm(name) -> str:
    s = re.sub(r"[^a-z0-9 .]", " ", str(name or "").lower())
    s = re.sub(r"\s+", " ", s).strip(" .")
    return ALIASES.get(s, s)


def coords(name) -> tuple[float, float] | None:
    """Centre point of an area name as people write it ('Kondapur', 'Hitech City', 'S R Nagar', 'Kukatpally, Hyd')."""
    s = _norm(name)
    if not s:
        return None
    if s in PLACES:
        return PLACES[s]
    for part in re.split(r"[,/]| near | opp | beside ", s):  # "Kondapur, Hyderabad" / "near Kondapur"
        p = ALIASES.get(part.strip(" ."), part.strip(" ."))
        if p in PLACES:
            return PLACES[p]
    for key in sorted(PLACES, key=len, reverse=True):  # "kondapur main road", "opp kphb metro"
        if re.search(r"\b" + re.escape(key) + r"\b", s):
            return PLACES[key]
    return None


def distance_km(a, b) -> float | None:
    """Straight-line distance between two area names (or (lat, lng) pairs), in km; None when either is unknown."""
    pa = a if isinstance(a, tuple) else coords(a)
    pb = b if isinstance(b, tuple) else coords(b)
    if not pa or not pb:
        return None
    lat1, lon1, lat2, lon2 = map(math.radians, (*pa, *pb))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return round(6371 * 2 * math.asin(math.sqrt(h)), 1)


def nearest(place, candidates: list[str]) -> tuple[str | None, float | None]:
    """The closest of `candidates` to `place`: (name, km)."""
    best = (None, None)
    for c in candidates:
        d = distance_km(place, c)
        if d is not None and (best[1] is None or d < best[1]):
            best = (c, d)
    return best


def known_area_names() -> list[str]:
    return sorted(n.title().replace("Lb ", "LB ").replace("Sr ", "SR ").replace("Kphb", "KPHB")
                  .replace("Ecil", "ECIL").replace("Jntu", "JNTU").replace("As Rao", "AS Rao")
                  .replace("Bn Reddy", "BN Reddy").replace("Hitec", "HITEC") for n in PLACES)


def _city_map() -> dict[str, str]:
    """area key → city name, from the order of PLACES (each block of areas belongs to one city)."""
    keys = list(PLACES)
    blocks = [("Hyderabad", None, "hyderabad"), ("Bengaluru", "koramangala", "t nagar"),
              ("Chennai", "t nagar", "mvp colony"), ("Visakhapatnam", "mvp colony", "benz circle"),
              ("Vijayawada", "benz circle", "kakkanad"), ("Kochi", "kakkanad", "rs puram"),
              ("Coimbatore", "rs puram", None)]
    out = {}
    for city, start, stop in blocks:
        i = keys.index(start) if start else 0
        j = keys.index(stop) if stop else len(keys)
        for k in keys[i:j]:
            out[k] = city
    for k in keys[keys.index("hyderabad"):keys.index("koramangala")]:  # city centres name themselves
        out[k] = k.title()
    out.update({"vizag": "Visakhapatnam", "bengaluru": "Bengaluru", "bangalore": "Bengaluru", "cochin": "Kochi",
                "trivandrum": "Thiruvananthapuram", "calicut": "Kozhikode", "pondicherry": "Puducherry",
                "mysuru": "Mysuru", "mysore": "Mysuru", "mangalore": "Mangaluru", "belgaum": "Belagavi",
                "gulbarga": "Kalaburagi", "tiruchirappalli": "Tiruchirappalli", "trichy": "Tiruchirappalli"})
    return out


CITY_OF = _city_map()


def _key(name) -> str | None:
    """The PLACES key for a name as people write it (same rules as coords)."""
    pt = coords(name)
    if not pt:
        return None
    s = _norm(name)
    if s in PLACES:
        return s
    for part in re.split(r"[,/]", str(name or "").lower()):
        p = _norm(part)
        if p in PLACES:
            return p
    for key in sorted(PLACES, key=len, reverse=True):
        if re.search(r"\b" + re.escape(key) + r"\b", s):
            return key
    return None


def city_of(name) -> str | None:
    """'Kondapur' → 'Hyderabad', 'Koramangala' → 'Bengaluru', 'Vizag' → 'Visakhapatnam'; None when unknown."""
    k = _key(name)
    return CITY_OF.get(k) if k else None


def nearest_area(lat: float, lng: float, max_km: float = 15) -> tuple[str | None, str | None]:
    """The closest known area to a point (from the phone's location): (Area name, City) or (None, None)."""
    best, best_d = None, None
    for k, pt in PLACES.items():
        if CITY_OF.get(k, "").lower() == k:  # skip city centres when an area is close
            continue
        d = distance_km((lat, lng), pt)
        if d is not None and (best_d is None or d < best_d):
            best, best_d = k, d
    if best is None or best_d > max_km:
        for k, pt in PLACES.items():  # far from any listed area: the nearest city centre
            d = distance_km((lat, lng), pt)
            if d is not None and (best_d is None or d < best_d):
                best, best_d = k, d
        if best_d is None or best_d > 60:
            return None, None
    title = dict((n.lower(), n) for n in known_area_names()).get(best, best.title())
    return title, CITY_OF.get(best)
