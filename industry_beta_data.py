"""Damodaran's industry average betas. GENERATED - do not edit by hand.

Regenerate with `python tools/refresh_industry_betas.py`, which fetches the
spreadsheet below and rewrites this file. The hand-written part of this
subject - which SIC code belongs to which of these industries - is in
`industry_betas.py`, and is deliberately kept out of a generated file.

Each entry holds the industry's unlevered beta corrected for cash, which is
the figure that can be relevered onto a particular company's capital
structure, together with the averages it was computed from so a reader can
see how thin the average is. `firms` is the one to look at first: an average
over four companies is not the same kind of evidence as an average over five
hundred.

    source:  https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls
    as of:   2026-01-05
    fetched: 2026-11-18
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class IndustryBeta:
    """One row of Damodaran's industry table."""

    name: str
    firms: int
    levered_beta: float
    unlevered_beta: float       # corrected for cash
    debt_to_equity: float
    tax_rate: float


# Damodaran dates the table by the data it was computed from, which is the
# start of the calendar year, not the day it was downloaded.
DAMODARAN_BETAS_AS_OF = date(2026, 1, 5)
DAMODARAN_BETAS_URL = ('https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls')

# The whole-market row, used when a company's SIC code has no industry in
# this table - which is an honest "we do not know this company's industry"
# and not a guess at one.
TOTAL_MARKET = "Total Market"

INDUSTRY_BETAS: dict[str, IndustryBeta] = {
    'Advertising': IndustryBeta(
        'Advertising', 52, 1.2105, 1.0080,
        0.4020, 0.0502),
    'Aerospace/Defense': IndustryBeta(
        'Aerospace/Defense', 79, 0.9455, 0.8694,
        0.1556, 0.1158),
    'Air Transport': IndustryBeta(
        'Air Transport', 23, 1.1855, 0.7579,
        0.9117, 0.0829),
    'Apparel': IndustryBeta(
        'Apparel', 35, 0.9359, 0.7946,
        0.3129, 0.0961),
    'Auto & Truck': IndustryBeta(
        'Auto & Truck', 33, 1.4565, 1.3082,
        0.1970, 0.0374),
    'Auto Parts': IndustryBeta(
        'Auto Parts', 35, 1.3394, 1.1283,
        0.4146, 0.1500),
    'Bank (Money Center)': IndustryBeta(
        'Bank (Money Center)', 15, 0.7610, 0.4439,
        1.6419, 0.1843),
    'Banks (Regional)': IndustryBeta(
        'Banks (Regional)', 568, 0.3985, 0.3744,
        0.5210, 0.1761),
    'Beverage (Alcoholic)': IndustryBeta(
        'Beverage (Alcoholic)', 14, 0.8125, 0.6280,
        0.4334, 0.1235),
    'Beverage (Soft)': IndustryBeta(
        'Beverage (Soft)', 27, 0.6415, 0.5755,
        0.2059, 0.0685),
    'Broadcasting': IndustryBeta(
        'Broadcasting', 24, 0.4708, 0.3153,
        0.8585, 0.0773),
    'Brokerage & Investment Banking': IndustryBeta(
        'Brokerage & Investment Banking', 32, 1.1715, 0.6794,
        1.3557, 0.1527),
    'Building Materials': IndustryBeta(
        'Building Materials', 41, 1.1115, 0.9605,
        0.2600, 0.1802),
    'Business & Consumer Services': IndustryBeta(
        'Business & Consumer Services', 155, 0.8880, 0.8060,
        0.1972, 0.1038),
    'Cable TV': IndustryBeta(
        'Cable TV', 9, 0.7407, 0.3629,
        1.4694, 0.1064),
    'Chemical (Basic)': IndustryBeta(
        'Chemical (Basic)', 29, 1.0122, 0.6368,
        0.9935, 0.0768),
    'Chemical (Diversified)': IndustryBeta(
        'Chemical (Diversified)', 4, 0.8507, 0.4061,
        1.7611, 0.0000),
    'Chemical (Specialty)': IndustryBeta(
        'Chemical (Specialty)', 59, 0.9698, 0.8245,
        0.2988, 0.1368),
    'Coal & Related Energy': IndustryBeta(
        'Coal & Related Energy', 16, 1.0710, 1.1824,
        0.0714, 0.0312),
    'Computer Services': IndustryBeta(
        'Computer Services', 64, 1.0879, 0.9617,
        0.2510, 0.1053),
    'Computers/Peripherals': IndustryBeta(
        'Computers/Peripherals', 36, 1.3503, 1.3246,
        0.0462, 0.0591),
    'Construction Supplies': IndustryBeta(
        'Construction Supplies', 40, 1.1504, 1.0468,
        0.1762, 0.1604),
    'Diversified': IndustryBeta(
        'Diversified', 20, 0.8810, 0.8431,
        0.1555, 0.0276),
    'Drugs (Biotechnology)': IndustryBeta(
        'Drugs (Biotechnology)', 496, 1.1353, 1.0795,
        0.1304, 0.0108),
    'Drugs (Pharmaceutical)': IndustryBeta(
        'Drugs (Pharmaceutical)', 228, 0.9828, 0.9151,
        0.1454, 0.0299),
    'Education': IndustryBeta(
        'Education', 32, 0.7809, 0.7196,
        0.2438, 0.1554),
    'Electrical Equipment': IndustryBeta(
        'Electrical Equipment', 112, 1.2511, 1.1898,
        0.1200, 0.0482),
    'Electronics (Consumer & Office)': IndustryBeta(
        'Electronics (Consumer & Office)', 8, 0.8660, 0.9274,
        0.0580, 0.0000),
    'Electronics (General)': IndustryBeta(
        'Electronics (General)', 114, 0.9715, 0.9375,
        0.1101, 0.0804),
    'Engineering/Construction': IndustryBeta(
        'Engineering/Construction', 48, 1.2100, 1.1374,
        0.1401, 0.1364),
    'Entertainment': IndustryBeta(
        'Entertainment', 92, 0.8252, 0.7628,
        0.1591, 0.0330),
    'Environmental & Waste Services': IndustryBeta(
        'Environmental & Waste Services', 53, 0.9455, 0.8248,
        0.2145, 0.0427),
    'Farming/Agriculture': IndustryBeta(
        'Farming/Agriculture', 35, 1.1292, 0.8458,
        0.5185, 0.0629),
    'Financial Svcs. (Non-bank & Insurance)': IndustryBeta(
        'Financial Svcs. (Non-bank & Insurance)', 176, 0.9697, 0.3277,
        2.7213, 0.1206),
    'Food Processing': IndustryBeta(
        'Food Processing', 78, 0.6075, 0.4694,
        0.4373, 0.1037),
    'Food Wholesalers': IndustryBeta(
        'Food Wholesalers', 13, 0.8666, 0.6478,
        0.4697, 0.0915),
    'Furn/Home Furnishings': IndustryBeta(
        'Furn/Home Furnishings', 27, 0.8232, 0.6521,
        0.4233, 0.1138),
    'Green & Renewable Energy': IndustryBeta(
        'Green & Renewable Energy', 15, 0.8563, 0.4723,
        1.1311, 0.0000),
    'Healthcare Products': IndustryBeta(
        'Healthcare Products', 204, 0.9086, 0.8569,
        0.1279, 0.0485),
    'Healthcare Support Services': IndustryBeta(
        'Healthcare Support Services', 104, 0.8720, 0.7449,
        0.3543, 0.0980),
    'Heathcare Information and Technology': IndustryBeta(
        'Heathcare Information and Technology', 115, 1.1083, 1.0163,
        0.1574, 0.0638),
    'Homebuilding': IndustryBeta(
        'Homebuilding', 30, 0.9106, 0.8532,
        0.2134, 0.1699),
    'Hospitals/Healthcare Facilities': IndustryBeta(
        'Hospitals/Healthcare Facilities', 31, 0.7997, 0.5649,
        0.5992, 0.1135),
    'Hotel/Gaming': IndustryBeta(
        'Hotel/Gaming', 63, 1.0804, 0.8757,
        0.3975, 0.0831),
    'Household Products': IndustryBeta(
        'Household Products', 110, 0.8155, 0.7411,
        0.1815, 0.0650),
    'Information Services': IndustryBeta(
        'Information Services', 15, 0.9206, 0.7564,
        0.3317, 0.1816),
    'Insurance (General)': IndustryBeta(
        'Insurance (General)', 21, 0.6720, 0.5781,
        0.2563, 0.1277),
    'Insurance (Life)': IndustryBeta(
        'Insurance (Life)', 20, 0.6445, 0.5317,
        0.6784, 0.1519),
    'Insurance (Prop/Cas.)': IndustryBeta(
        'Insurance (Prop/Cas.)', 57, 0.4841, 0.4570,
        0.1483, 0.1837),
    'Investments & Asset Management': IndustryBeta(
        'Investments & Asset Management', 283, 0.6596, 0.5881,
        0.3269, 0.0353),
    'Machinery': IndustryBeta(
        'Machinery', 105, 0.9636, 0.8940,
        0.1469, 0.1337),
    'Metals & Mining': IndustryBeta(
        'Metals & Mining', 73, 1.0430, 1.0104,
        0.1098, 0.0252),
    'Office Equipment & Services': IndustryBeta(
        'Office Equipment & Services', 14, 1.3344, 1.0382,
        0.4810, 0.1230),
    'Oil/Gas (Integrated)': IndustryBeta(
        'Oil/Gas (Integrated)', 4, 0.2994, 0.2780,
        0.1385, 0.2824),
    'Oil/Gas (Production and Exploration)': IndustryBeta(
        'Oil/Gas (Production and Exploration)', 142, 0.7214, 0.5785,
        0.3759, 0.0666),
    'Oil/Gas Distribution': IndustryBeta(
        'Oil/Gas Distribution', 23, 0.6692, 0.4693,
        0.5853, 0.0917),
    'Oilfield Svcs/Equip.': IndustryBeta(
        'Oilfield Svcs/Equip.', 97, 0.9512, 0.7871,
        0.3736, 0.0875),
    'Packaging & Container': IndustryBeta(
        'Packaging & Container', 19, 1.0219, 0.7490,
        0.5511, 0.1557),
    'Paper/Forest Products': IndustryBeta(
        'Paper/Forest Products', 6, 0.9580, 0.7697,
        0.4369, 0.0860),
    'Power': IndustryBeta(
        'Power', 46, 0.4824, 0.3146,
        0.7415, 0.1275),
    'Precious Metals': IndustryBeta(
        'Precious Metals', 56, 0.8366, 0.8309,
        0.0728, 0.0597),
    'Publishing & Newspapers': IndustryBeta(
        'Publishing & Newspapers', 19, 0.5630, 0.5119,
        0.2394, 0.1021),
    'R.E.I.T.': IndustryBeta(
        'R.E.I.T.', 190, 0.6418, 0.4005,
        0.8446, 0.0158),
    'Real Estate (Development)': IndustryBeta(
        'Real Estate (Development)', 14, 0.8436, 0.5641,
        1.0183, 0.0491),
    'Real Estate (General/Diversified)': IndustryBeta(
        'Real Estate (General/Diversified)', 12, 0.8100, 0.6270,
        0.5356, 0.0465),
    'Real Estate (Operations & Services)': IndustryBeta(
        'Real Estate (Operations & Services)', 54, 0.9655, 0.8576,
        0.2464, 0.0870),
    'Recreation': IndustryBeta(
        'Recreation', 49, 1.0235, 0.7365,
        0.6299, 0.1145),
    'Reinsurance': IndustryBeta(
        'Reinsurance', 1, 0.5813, 0.5777,
        0.4347, 0.3036),
    'Restaurant/Dining': IndustryBeta(
        'Restaurant/Dining', 64, 0.9241, 0.7830,
        0.2722, 0.0992),
    'Retail (Automotive)': IndustryBeta(
        'Retail (Automotive)', 34, 0.9360, 0.7132,
        0.4536, 0.1077),
    'Retail (Building Supply)': IndustryBeta(
        'Retail (Building Supply)', 14, 1.5353, 1.3177,
        0.2329, 0.1184),
    'Retail (Distributors)': IndustryBeta(
        'Retail (Distributors)', 62, 0.9484, 0.8016,
        0.2824, 0.1459),
    'Retail (General)': IndustryBeta(
        'Retail (General)', 23, 0.8052, 0.7809,
        0.0794, 0.1910),
    'Retail (Grocery and Food)': IndustryBeta(
        'Retail (Grocery and Food)', 15, 1.1183, 0.8484,
        0.5195, 0.1222),
    'Retail (REITs)': IndustryBeta(
        'Retail (REITs)', 26, 0.6207, 0.4427,
        0.5642, 0.0160),
    'Retail (Special Lines)': IndustryBeta(
        'Retail (Special Lines)', 94, 1.0894, 1.0019,
        0.1976, 0.1007),
    'Rubber& Tires': IndustryBeta(
        'Rubber& Tires', 3, 0.5294, 0.1544,
        3.5847, 0.0000),
    'Semiconductor': IndustryBeta(
        'Semiconductor', 66, 1.5182, 1.5046,
        0.0259, 0.0511),
    'Semiconductor Equip': IndustryBeta(
        'Semiconductor Equip', 31, 1.3972, 1.3917,
        0.0486, 0.0996),
    'Shipbuilding & Marine': IndustryBeta(
        'Shipbuilding & Marine', 8, 0.7529, 0.6608,
        0.2255, 0.0526),
    'Shoe': IndustryBeta(
        'Shoe', 11, 1.0173, 1.0002,
        0.1194, 0.1186),
    'Software (Entertainment)': IndustryBeta(
        'Software (Entertainment)', 77, 1.0283, 1.0207,
        0.0204, 0.0529),
    'Software (Internet)': IndustryBeta(
        'Software (Internet)', 29, 1.6887, 1.5905,
        0.1230, 0.0305),
    'Software (System & Application)': IndustryBeta(
        'Software (System & Application)', 309, 1.2766, 1.2482,
        0.0558, 0.0551),
    'Steel': IndustryBeta(
        'Steel', 19, 1.0628, 0.9449,
        0.2351, 0.0928),
    'Telecom (Wireless)': IndustryBeta(
        'Telecom (Wireless)', 12, 0.5381, 0.3924,
        0.5195, 0.0402),
    'Telecom. Equipment': IndustryBeta(
        'Telecom. Equipment', 57, 0.9232, 0.8873,
        0.0922, 0.0706),
    'Telecom. Services': IndustryBeta(
        'Telecom. Services', 39, 0.6287, 0.3815,
        0.9606, 0.0340),
    'Tobacco': IndustryBeta(
        'Tobacco', 10, 0.7943, 0.6903,
        0.2297, 0.1477),
    'Transportation': IndustryBeta(
        'Transportation', 19, 0.8599, 0.7115,
        0.3645, 0.0854),
    'Transportation (Railroads)': IndustryBeta(
        'Transportation (Railroads)', 4, 0.9751, 0.8137,
        0.2779, 0.1698),
    'Trucking': IndustryBeta(
        'Trucking', 26, 1.0113, 0.8689,
        0.2523, 0.1260),
    'Utility (General)': IndustryBeta(
        'Utility (General)', 14, 0.2394, 0.1491,
        0.8148, 0.1277),
    'Utility (Water)': IndustryBeta(
        'Utility (Water)', 14, 0.4127, 0.2825,
        0.6236, 0.1119),
    'Total Market': IndustryBeta(
        'Total Market', 5994, 0.9121, 0.7557,
        0.3517, 0.0830),
    'Total Market (without financials)': IndustryBeta(
        'Total Market (without financials)', 4822, 0.9913, 0.9010,
        0.1729, 0.0710),
}
