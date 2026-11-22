"""Damodaran's industry investment intensities. GENERATED - do not edit by hand.

Regenerate with `python tools/refresh_industry_capex.py`, which fetches the
spreadsheet below and rewrites this file. Which SIC code belongs to which of
these industries is the hand-written part, and it lives in `industry_betas.py`
- the same map serves both tables, because they are the same industries.

Published ratios only. What a mature company's investment and depreciation
settle at is a judgement, and it lives in `reinvestment.py` rather than in a
generated file.

    source:  https://pages.stern.nyu.edu/~adamodar/pc/datasets/capex.xls
    as of:   2026-01-05
    fetched: 2026-11-22
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class IndustryCapex:
    """One row of Damodaran's capital-expenditure table."""

    name: str
    firms: int
    capex_to_depreciation: float
    net_capex_to_sales: float
    sales_to_invested_capital: float


DAMODARAN_CAPEX_AS_OF = date(2026, 1, 5)
DAMODARAN_CAPEX_URL = ('https://pages.stern.nyu.edu/~adamodar/pc/datasets/capex.xls')

# The whole-market row, used when a company's SIC code has no industry in
# this table - an honest "we do not know this company's industry" rather
# than a guess at one.
TOTAL_MARKET = "Total Market"

INDUSTRY_CAPEX: dict[str, IndustryCapex] = {
    'Advertising': IndustryCapex(
        'Advertising', 52,
        0.374254, -0.015213,
        3.853284),
    'Aerospace/Defense': IndustryCapex(
        'Aerospace/Defense', 79,
        0.954667, 0.008180,
        2.743410),
    'Air Transport': IndustryCapex(
        'Air Transport', 23,
        1.757544, 0.036066,
        1.799884),
    'Apparel': IndustryCapex(
        'Apparel', 35,
        1.334828, 0.020765,
        1.768667),
    'Auto & Truck': IndustryCapex(
        'Auto & Truck', 33,
        1.488743, 0.020963,
        1.075296),
    'Auto Parts': IndustryCapex(
        'Auto Parts', 35,
        1.451833, 0.020126,
        2.389160),
    'Beverage (Alcoholic)': IndustryCapex(
        'Beverage (Alcoholic)', 14,
        2.170287, 0.037360,
        0.788311),
    'Beverage (Soft)': IndustryCapex(
        'Beverage (Soft)', 27,
        1.591753, 0.046903,
        1.544179),
    'Broadcasting': IndustryCapex(
        'Broadcasting', 24,
        0.424669, -0.022589,
        1.278665),
    'Brokerage & Investment Banking': IndustryCapex(
        'Brokerage & Investment Banking', 32,
        1.702189, 0.016386,
        0.274868),
    'Building Materials': IndustryCapex(
        'Building Materials', 41,
        1.086337, 0.027685,
        2.060798),
    'Business & Consumer Services': IndustryCapex(
        'Business & Consumer Services', 155,
        0.652473, 0.033591,
        2.798511),
    'Cable TV': IndustryCapex(
        'Cable TV', 9,
        0.901069, -0.007790,
        0.743398),
    'Chemical (Basic)': IndustryCapex(
        'Chemical (Basic)', 29,
        2.488807, 0.039431,
        1.490160),
    'Chemical (Diversified)': IndustryCapex(
        'Chemical (Diversified)', 4,
        0.781623, -0.011810,
        1.265179),
    'Chemical (Specialty)': IndustryCapex(
        'Chemical (Specialty)', 59,
        1.552805, 0.040549,
        1.105419),
    'Coal & Related Energy': IndustryCapex(
        'Coal & Related Energy', 16,
        0.826825, -0.031172,
        1.291228),
    'Computer Services': IndustryCapex(
        'Computer Services', 64,
        0.435953, 0.019118,
        5.189891),
    'Computers/Peripherals': IndustryCapex(
        'Computers/Peripherals', 36,
        1.198435, 0.028994,
        3.619789),
    'Construction Supplies': IndustryCapex(
        'Construction Supplies', 40,
        1.679666, 0.066163,
        1.446946),
    'Diversified': IndustryCapex(
        'Diversified', 20,
        1.348826, 0.023159,
        0.676059),
    'Drugs (Biotechnology)': IndustryCapex(
        'Drugs (Biotechnology)', 496,
        0.325471, -0.035742,
        0.819955),
    'Drugs (Pharmaceutical)': IndustryCapex(
        'Drugs (Pharmaceutical)', 228,
        0.804750, 0.011179,
        1.111659),
    'Education': IndustryCapex(
        'Education', 32,
        0.873000, 0.006485,
        1.627454),
    'Electrical Equipment': IndustryCapex(
        'Electrical Equipment', 112,
        1.110576, 0.030840,
        2.029529),
    'Electronics (Consumer & Office)': IndustryCapex(
        'Electronics (Consumer & Office)', 8,
        0.439866, -0.038403,
        5.566546),
    'Electronics (General)': IndustryCapex(
        'Electronics (General)', 114,
        1.410902, 0.056801,
        2.383494),
    'Engineering/Construction': IndustryCapex(
        'Engineering/Construction', 48,
        1.243130, 0.048467,
        4.361449),
    'Entertainment': IndustryCapex(
        'Entertainment', 92,
        0.630360, -0.012526,
        1.391406),
    'Environmental & Waste Services': IndustryCapex(
        'Environmental & Waste Services', 53,
        1.101424, 0.134384,
        2.299851),
    'Farming/Agriculture': IndustryCapex(
        'Farming/Agriculture', 35,
        1.780309, 0.036582,
        1.479435),
    'Financial Svcs. (Non-bank & Insurance)': IndustryCapex(
        'Financial Svcs. (Non-bank & Insurance)', 176,
        0.897381, 0.023803,
        0.070856),
    'Food Processing': IndustryCapex(
        'Food Processing', 78,
        1.343524, 0.022867,
        1.709199),
    'Food Wholesalers': IndustryCapex(
        'Food Wholesalers', 13,
        2.281383, 0.015040,
        7.712910),
    'Furn/Home Furnishings': IndustryCapex(
        'Furn/Home Furnishings', 27,
        1.114083, 0.058771,
        1.958544),
    'Green & Renewable Energy': IndustryCapex(
        'Green & Renewable Energy', 15,
        0.916756, 0.090074,
        0.197995),
    'Healthcare Products': IndustryCapex(
        'Healthcare Products', 204,
        0.795388, 0.045266,
        1.476950),
    'Healthcare Support Services': IndustryCapex(
        'Healthcare Support Services', 104,
        0.807284, 0.008657,
        11.746627),
    'Heathcare Information and Technology': IndustryCapex(
        'Heathcare Information and Technology', 115,
        0.543995, 0.009216,
        1.245699),
    'Homebuilding': IndustryCapex(
        'Homebuilding', 30,
        0.889471, 0.006828,
        1.421519),
    'Hospitals/Healthcare Facilities': IndustryCapex(
        'Hospitals/Healthcare Facilities', 31,
        1.407293, 0.029612,
        1.950309),
    'Hotel/Gaming': IndustryCapex(
        'Hotel/Gaming', 63,
        1.041939, 0.025178,
        1.039900),
    'Household Products': IndustryCapex(
        'Household Products', 110,
        1.067243, 0.011320,
        2.214429),
    'Information Services': IndustryCapex(
        'Information Services', 15,
        0.351925, -0.028717,
        2.512143),
    'Insurance (General)': IndustryCapex(
        'Insurance (General)', 21,
        0.238074, 0.550430,
        2.409978),
    'Insurance (Prop/Cas.)': IndustryCapex(
        'Insurance (Prop/Cas.)', 57,
        0.366311, -0.010506,
        1.480298),
    'Machinery': IndustryCapex(
        'Machinery', 105,
        0.678567, 0.041351,
        1.951926),
    'Metals & Mining': IndustryCapex(
        'Metals & Mining', 73,
        1.643683, 0.046138,
        1.175716),
    'Office Equipment & Services': IndustryCapex(
        'Office Equipment & Services', 14,
        1.208740, 0.018695,
        2.205031),
    'Oil/Gas (Integrated)': IndustryCapex(
        'Oil/Gas (Integrated)', 4,
        1.021903, 0.003890,
        1.024311),
    'Oil/Gas (Production and Exploration)': IndustryCapex(
        'Oil/Gas (Production and Exploration)', 142,
        1.568909, 0.190487,
        0.574099),
    'Oil/Gas Distribution': IndustryCapex(
        'Oil/Gas Distribution', 23,
        2.125008, 0.195920,
        0.521937),
    'Oilfield Svcs/Equip.': IndustryCapex(
        'Oilfield Svcs/Equip.', 97,
        0.920917, 0.009081,
        2.792715),
    'Packaging & Container': IndustryCapex(
        'Packaging & Container', 19,
        0.898391, 0.051378,
        1.840381),
    'Paper/Forest Products': IndustryCapex(
        'Paper/Forest Products', 6,
        1.505724, 0.018007,
        1.731798),
    'Power': IndustryCapex(
        'Power', 46,
        2.605369, 0.243461,
        0.371734),
    'Precious Metals': IndustryCapex(
        'Precious Metals', 56,
        1.530322, 0.061196,
        0.677796),
    'Publishing & Newspapers': IndustryCapex(
        'Publishing & Newspapers', 19,
        0.693251, -0.005904,
        2.164890),
    'R.E.I.T.': IndustryCapex(
        'R.E.I.T.', 190,
        0.169869, -0.127112,
        0.146543),
    'Real Estate (Development)': IndustryCapex(
        'Real Estate (Development)', 14,
        0.197860, -0.042704,
        0.349511),
    'Real Estate (General/Diversified)': IndustryCapex(
        'Real Estate (General/Diversified)', 12,
        0.313031, 0.004439,
        0.246662),
    'Real Estate (Operations & Services)': IndustryCapex(
        'Real Estate (Operations & Services)', 54,
        0.605764, 0.024639,
        2.516899),
    'Recreation': IndustryCapex(
        'Recreation', 49,
        1.123797, 0.013354,
        1.242007),
    'Restaurant/Dining': IndustryCapex(
        'Restaurant/Dining', 64,
        1.635648, 0.036916,
        1.512857),
    'Retail (Automotive)': IndustryCapex(
        'Retail (Automotive)', 34,
        1.656176, 0.022506,
        2.341077),
    'Retail (Building Supply)': IndustryCapex(
        'Retail (Building Supply)', 14,
        1.173538, 0.057548,
        3.407057),
    'Retail (Distributors)': IndustryCapex(
        'Retail (Distributors)', 62,
        4.454778, 0.115793,
        1.830131),
    'Retail (General)': IndustryCapex(
        'Retail (General)', 23,
        1.913393, 0.048662,
        3.506318),
    'Retail (REITs)': IndustryCapex(
        'Retail (REITs)', 26,
        0.160914, -0.228547,
        0.135814),
    'Retail (Special Lines)': IndustryCapex(
        'Retail (Special Lines)', 94,
        1.654024, 0.015903,
        2.988447),
    'Rubber& Tires': IndustryCapex(
        'Rubber& Tires', 3,
        1.008749, -0.000326,
        1.394168),
    'Semiconductor': IndustryCapex(
        'Semiconductor', 66,
        1.267335, 0.044119,
        1.206668),
    'Semiconductor Equip': IndustryCapex(
        'Semiconductor Equip', 31,
        2.004356, 0.041369,
        1.851109),
    'Shipbuilding & Marine': IndustryCapex(
        'Shipbuilding & Marine', 8,
        1.672015, 0.048919,
        0.936401),
    'Shoe': IndustryCapex(
        'Shoe', 11,
        0.847765, 0.003993,
        2.622100),
    'Software (Entertainment)': IndustryCapex(
        'Software (Entertainment)', 77,
        3.662054, 0.195211,
        1.344742),
    'Software (Internet)': IndustryCapex(
        'Software (Internet)', 29,
        3.542884, 0.260111,
        1.350097),
    'Software (System & Application)': IndustryCapex(
        'Software (System & Application)', 309,
        2.445507, 0.178207,
        1.538172),
    'Steel': IndustryCapex(
        'Steel', 19,
        1.812480, 0.056934,
        1.803683),
    'Telecom (Wireless)': IndustryCapex(
        'Telecom (Wireless)', 12,
        0.761662, 0.003819,
        0.547756),
    'Telecom. Equipment': IndustryCapex(
        'Telecom. Equipment', 57,
        1.261650, 0.070726,
        2.567714),
    'Telecom. Services': IndustryCapex(
        'Telecom. Services', 39,
        1.049178, 0.009079,
        0.602873),
    'Tobacco': IndustryCapex(
        'Tobacco', 10,
        0.711600, -0.009101,
        1.824170),
    'Total Market': IndustryCapex(
        'Total Market', 5994,
        1.485735, 0.042312,
        0.821785),
    'Total Market (without financials)': IndustryCapex(
        'Total Market (without financials)', 4822,
        1.447430, 0.042915,
        1.433776),
    'Transportation': IndustryCapex(
        'Transportation', 19,
        1.495580, 0.022045,
        1.975348),
    'Transportation (Railroads)': IndustryCapex(
        'Transportation (Railroads)', 4,
        1.625875, 0.064676,
        0.458134),
    'Trucking': IndustryCapex(
        'Trucking', 26,
        1.499824, 0.050342,
        1.465721),
    'Utility (General)': IndustryCapex(
        'Utility (General)', 14,
        3.150448, 0.335759,
        0.296133),
    'Utility (Water)': IndustryCapex(
        'Utility (Water)', 14,
        3.532886, 0.437246,
        0.242821),
}
