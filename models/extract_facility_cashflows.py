#!/usr/bin/env python
"""
Extract and analyze Permanent Facility Agreement cash flows.
Back out the implied index growth rate for XS0316010023.
"""

import QuantLib as ql
from datetime import datetime
from pathlib import Path

try:
    from models import spire
except ModuleNotFoundError:
    import spire


def extract_facility_cashflows():
    """
    Extract cash flows from Permanent Facility Agreement amortization schedules.
    Returns aggregated annual and semiannual CF through note maturity (30-06-2041).
    """
    
    # Amortization data extracted from CLEF prospectus document
    # Principal repayments in currency (GBP £, EUR €)
    amortization_schedule = {
        'A1': {  # £750M, inflation-linked 3.487%, 20-Jun-2018 to 20-Jun-2042
            'currency': 'GBP',
            'principal': 750_000_000.0,
            'coupon_rate': 0.034870,
            'coupon_type': 'inflation_linked',
            'payments': {
                '2018-06-20': 14_895_038.22,
                '2018-12-20': 7_848_940.39,
                '2019-06-20': 7_848_940.39,
                '2019-12-20': 8_271_998.28,
                '2020-06-20': 8_271_998.28,
                '2020-12-20': 8_717_858.98,
                '2021-06-20': 8_717_858.98,
                '2021-12-20': 9_187_751.58,
                '2022-06-20': 9_187_751.58,
                '2022-12-20': 9_682_971.39,
                '2023-06-20': 9_682_971.39,
                '2023-12-20': 10_204_883.55,
                '2024-06-20': 10_204_883.55,
                '2024-12-20': 10_754_926.78,
                '2025-06-20': 10_754_926.78,
                '2025-12-20': 11_334_617.33,
                '2026-06-20': 11_334_617.33,
                '2026-12-20': 11_945_553.20,
                '2027-06-20': 11_945_553.20,
                '2027-12-20': 12_589_418.52,
                '2028-06-20': 12_589_418.52,
                '2028-12-20': 13_267_988.18,
                '2029-06-20': 13_267_988.18,
                '2029-12-20': 13_983_132.74,
                '2030-06-20': 13_983_132.74,
                '2030-12-20': 14_736_823.60,
                '2031-06-20': 14_736_823.60,
                '2031-12-20': 15_531_138.39,
                '2032-06-20': 15_531_138.39,
                '2032-12-20': 16_368_266.75,
                '2033-06-20': 16_368_266.75,
                '2033-12-20': 17_250_516.33,
                '2034-06-20': 17_250_516.33,
                '2034-12-20': 18_180_319.16,
                '2035-06-20': 18_180_319.16,
                '2035-12-20': 19_160_238.36,
                '2036-06-20': 19_160_238.36,
                '2036-12-20': 20_192_975.21,
                '2037-06-20': 20_192_975.21,
                '2037-12-20': 21_281_376.57,
                '2038-06-20': 21_281_376.57,
                '2038-12-20': 22_428_442.77,
                '2039-06-20': 22_428_442.77,
                '2039-12-20': 23_637_335.83,
                '2040-06-20': 23_637_335.83,
                '2040-12-20': 24_911_388.23,
                '2041-06-20': 24_911_388.23,
            }
        },
        'A2': {  # €367M, inflation-linked 3.977%, 20-Jun-2018 to 20-Jun-2041
            'currency': 'EUR',
            'principal': 367_000_000.0,
            'coupon_rate': 0.039770,
            'coupon_type': 'inflation_linked',
            'payments': {
                '2018-06-20': 7_754_253.07,
                '2018-12-20': 4_106_771.80,
                '2019-06-20': 4_106_771.80,
                '2019-12-20': 4_350_019.13,
                '2020-06-20': 4_350_019.13,
                '2020-12-20': 4_607_674.20,
                '2021-06-20': 4_607_674.20,
                '2021-12-20': 4_880_590.37,
                '2022-06-20': 4_880_590.37,
                '2022-12-20': 5_169_671.59,
                '2023-06-20': 5_169_671.59,
                '2023-12-20': 5_475_875.32,
                '2024-06-20': 5_475_875.32,
                '2024-12-20': 5_800_215.73,
                '2025-06-20': 5_800_215.73,
                '2025-12-20': 6_143_767.08,
                '2026-06-20': 6_143_767.08,
                '2026-12-20': 6_507_667.26,
                '2027-06-20': 6_507_667.26,
                '2027-12-20': 6_893_121.52,
                '2028-06-20': 6_893_121.52,
                '2028-12-20': 7_301_406.55,
                '2029-06-20': 7_301_406.55,
                '2029-12-20': 7_733_874.61,
                '2030-06-20': 7_733_874.61,
                '2030-12-20': 8_191_958.11,
                '2031-06-20': 8_191_958.11,
                '2031-12-20': 8_677_174.25,
                '2032-06-20': 8_677_174.25,
                '2032-12-20': 9_191_130.12,
                '2033-06-20': 9_191_130.12,
                '2033-12-20': 9_735_528.01,
                '2034-06-20': 9_735_528.01,
                '2034-12-20': 10_312_171.01,
                '2035-06-20': 10_312_171.01,
                '2035-12-20': 10_922_969.04,
                '2036-06-20': 10_922_969.04,
                '2036-12-20': 11_569_945.11,
                '2037-06-20': 11_569_945.11,
                '2037-12-20': 12_255_242.09,
                '2038-06-20': 12_255_242.09,
                '2038-12-20': 12_981_129.74,
                '2039-06-20': 12_981_129.74,
                '2039-12-20': 13_750_012.30,
                '2040-06-20': 13_750_012.30,
                '2040-12-20': 3_064_958.53,
                '2041-06-20': 3_064_958.53,
            }
        },
        'B1': {  # £400M, fixed 6.631%, 20-Jun-2013 to 20-Jun-2046
            'currency': 'GBP',
            'principal': 400_000_000.0,
            'coupon_rate': 0.066310,
            'coupon_type': 'fixed',
            'payments': {
                '2013-06-20': 12_729_228.72,
                '2013-12-20': 6_707_667.07,
                '2014-06-20': 6_707_667.07,
                '2014-12-20': 7_069_210.33,
                '2015-06-20': 7_069_210.33,
                '2015-12-20': 7_450_240.77,
                '2016-06-20': 7_450_240.77,
                '2016-12-20': 7_851_808.74,
                '2017-06-20': 7_851_808.74,
                '2017-12-20': 827_502.12,
                '2018-06-20': 827_502.12,
                '2018-12-20': 872_104.49,
                '2019-06-20': 872_104.49,
                '2019-12-20': 919_110.92,
                '2020-06-20': 919_110.92,
                '2020-12-20': 968_651.00,
                '2021-06-20': 968_651.00,
                '2021-12-20': 1_020_861.29,
                '2022-06-20': 1_020_861.29,
                '2022-12-20': 1_075_885.71,
                '2023-06-20': 1_075_885.71,
                '2023-12-20': 1_133_875.95,
                '2024-06-20': 1_133_875.95,
                '2024-12-20': 1_194_991.86,
                '2025-06-20': 1_194_991.86,
                '2025-12-20': 1_259_401.93,
                '2026-06-20': 1_259_401.93,
                '2026-12-20': 1_327_283.69,
                '2027-06-20': 1_327_283.69,
                '2027-12-20': 1_398_824.28,
                '2028-06-20': 1_398_824.28,
                '2028-12-20': 1_474_220.91,
                '2029-06-20': 1_474_220.91,
                '2029-12-20': 1_553_681.42,
                '2030-06-20': 1_553_681.42,
                '2030-12-20': 1_637_424.84,
                '2031-06-20': 1_637_424.84,
                '2031-12-20': 1_725_682.04,
                '2032-06-20': 1_725_682.04,
                '2032-12-20': 1_818_696.31,
                '2033-06-20': 1_818_696.31,
                '2033-12-20': 1_916_724.04,
                '2034-06-20': 1_916_724.04,
                '2034-12-20': 2_020_035.46,
                '2035-06-20': 2_020_035.46,
                '2035-12-20': 2_128_915.37,
                '2036-06-20': 2_128_915.37,
                '2036-12-20': 2_243_663.91,
                '2037-06-20': 2_243_663.91,
                '2037-12-20': 2_364_597.40,
                '2038-06-20': 2_364_597.40,
                '2038-12-20': 2_492_049.20,
                '2039-06-20': 2_492_049.20,
                '2039-12-20': 2_626_370.65,
                '2040-06-20': 2_626_370.65,
                '2040-12-20': 2_767_932.03,
                '2041-06-20': 2_767_932.03,
                '2041-12-20': 3_087_616.85,
                '2042-06-20': 3_087_616.85,
                '2042-12-20': 30_743_565.22,
                '2043-06-20': 30_743_565.22,
                '2043-12-20': 32_400_643.39,
                '2044-06-20': 32_400_643.39,
                '2044-12-20': 34_147_038.05,
                '2045-06-20': 34_147_038.05,
                '2045-12-20': 25_409_108.40,
                '2046-06-20': 25_409_108.40,
            }
        },
        'B2': {  # €645M, fixed 6.182%, 20-Jun-2013 to 20-Jun-2041
            'currency': 'EUR',
            'principal': 645_000_000.0,
            'coupon_rate': 0.061820,
            'coupon_type': 'fixed',
            'payments': {
                '2013-06-20': 14_723_069.77,
                '2013-12-20': 7_797_564.40,
                '2014-06-20': 7_797_564.40,
                '2014-12-20': 8_259_420.29,
                '2015-06-20': 8_259_420.29,
                '2015-12-20': 8_748_632.27,
                '2016-06-20': 8_748_632.27,
                '2016-12-20': 9_266_820.66,
                '2017-06-20': 9_266_820.66,
                '2017-12-20': 5_938_575.22,
                '2018-06-20': 5_938_575.22,
                '2018-12-20': 6_290_321.72,
                '2019-06-20': 6_290_321.72,
                '2019-12-20': 6_662_902.44,
                '2020-06-20': 6_662_902.44,
                '2020-12-20': 7_057_551.40,
                '2021-06-20': 7_057_551.40,
                '2021-12-20': 7_475_575.74,
                '2022-06-20': 7_475_575.74,
                '2022-12-20': 7_918_359.99,
                '2023-06-20': 7_918_359.99,
                '2023-12-20': 8_387_370.69,
                '2024-06-20': 8_387_370.69,
                '2024-12-20': 8_884_161.28,
                '2025-06-20': 8_884_161.28,
                '2025-12-20': 9_410_377.16,
                '2026-06-20': 9_410_377.16,
                '2026-12-20': 9_967_761.22,
                '2027-06-20': 9_967_761.22,
                '2027-12-20': 10_558_159.58,
                '2028-06-20': 10_558_159.58,
                '2028-12-20': 11_183_527.70,
                '2029-06-20': 11_183_527.70,
                '2029-12-20': 11_845_936.87,
                '2030-06-20': 11_845_936.87,
                '2030-12-20': 12_547_581.05,
                '2031-06-20': 12_547_581.05,
                '2031-12-20': 13_290_784.17,
                '2032-06-20': 13_290_784.17,
                '2032-12-20': 14_078_007.80,
                '2033-06-20': 14_078_007.80,
                '2033-12-20': 14_911_859.31,
                '2034-06-20': 14_911_859.31,
                '2034-12-20': 15_795_100.50,
                '2035-06-20': 15_795_100.50,
                '2035-12-20': 16_730_656.76,
                '2036-06-20': 16_730_656.76,
                '2036-12-20': 17_721_626.76,
                '2037-06-20': 17_721_626.76,
                '2037-12-20': 18_771_292.69,
                '2038-06-20': 18_771_292.69,
                '2038-12-20': 19_883_131.17,
                '2039-06-20': 19_883_131.17,
                '2039-12-20': 21_060_824.71,
                '2040-06-20': 21_060_824.71,
                '2040-12-20': 4_694_581.57,
                '2041-06-20': 4_694_581.56,
            }
        },
        'C1': {  # £350M, floating LIBOR +1.39% (step-up Jun 2012), 20-Jun-2046 to 20-Jun-2050
            'currency': 'GBP',
            'principal': 350_000_000.0,
            'coupon_base': 'LIBOR',
            'coupon_spread': 0.0139,
            'coupon_type': 'floating',
            'amortization_start': '2046-06-20',
        },
        'C2': {  # €953M, floating EURIBOR +1.39% (step-up Jun 2012), 20-Jun-2041 to 20-Jun-2050
            'currency': 'EUR',
            'principal': 953_000_000.0,
            'coupon_base': 'EURIBOR',
            'coupon_spread': 0.0139,
            'coupon_type': 'floating',
            'payments': {
                '2041-06-20': 58_226_340.49,
            }
        }
    }
    
    note_maturity = ql.Date(30, 6, 2041)
    eval_date = ql.Date(10, 6, 2026)
    
    # Calculate total cash flows through note maturity
    total_cf_through_maturity = 0.0
    total_cf_annual = 0.0
    cf_by_date = {}
    
    for tranche_name, tranche_data in amortization_schedule.items():
        principal = tranche_data['principal']
        coupon_rate = tranche_data.get('coupon_rate', 0.0)
        coupon_type = tranche_data.get('coupon_type', 'fixed')
        
        if 'payments' in tranche_data:
            # Add scheduled principal repayments
            for date_str, principal_cf in tranche_data['payments'].items():
                date_obj = ql.Date(*[int(x) for x in date_str.split('-')])
                if date_obj <= note_maturity:
                    if date_str not in cf_by_date:
                        cf_by_date[date_str] = {'principal': 0.0, 'interest': 0.0}
                    cf_by_date[date_str]['principal'] += principal_cf
                    total_cf_through_maturity += principal_cf
    
    # Calculate interest cash flows (semiannual, 20 Jun and 20 Dec)
    for year in range(2007, 2042):
        for month in [6, 12]:
            date_str = f'{year:04d}-{month:02d}-20'
            date_obj = ql.Date(20, month, year)
            
            if date_obj > eval_date and date_obj <= note_maturity:
                # Interest on A1/A2/B1/B2 (C1/C2 don't contribute until later)
                for tranche_name in ['A1', 'A2', 'B1', 'B2']:
                    tranche = amortization_schedule[tranche_name]
                    principal_outstanding = tranche['principal']
                    
                    # Reduce principal by payments made before this date
                    if 'payments' in tranche:
                        for pdate_str, principal_cf in tranche['payments'].items():
                            pdate_obj = ql.Date(*[int(x) for x in pdate_str.split('-')])
                            if pdate_obj < date_obj:
                                principal_outstanding -= principal_cf
                    
                    coupon_rate = tranche.get('coupon_rate', 0.0)
                    
                    # Interest accrual (6-month period)
                    interest_cf = principal_outstanding * coupon_rate / 2.0
                    
                    if date_str not in cf_by_date:
                        cf_by_date[date_str] = {'principal': 0.0, 'interest': 0.0}
                    cf_by_date[date_str]['interest'] += interest_cf
                    total_cf_through_maturity += interest_cf
    
    # Calculate average annual cash flow
    years = (note_maturity - ql.Date(28, 6, 2007)) / 365.25
    avg_annual_cf = total_cf_through_maturity / years if years > 0 else 0.0
    
    # Implied index growth (proxy: average CF yield relative to initial facility size)
    total_initial_facility_eur_equiv = 2_840_000_000 * 0.7 + 1_965_000_000  # Rough GBP/EUR conversion
    implied_annual_yield = avg_annual_cf / total_initial_facility_eur_equiv if total_initial_facility_eur_equiv > 0 else 0.0
    
    return {
        'total_cf_through_maturity': total_cf_through_maturity,
        'avg_annual_cf': avg_annual_cf,
        'years_to_maturity': years,
        'implied_annual_yield': implied_annual_yield,
        'cf_by_date': cf_by_date,
    }


if __name__ == '__main__':
    result = extract_facility_cashflows()
    print(f"Total cash flows through note maturity (30-06-2041): €{result['total_cf_through_maturity']:,.2f}")
    print(f"Average annual cash flow: €{result['avg_annual_cf']:,.2f}")
    print(f"Years to maturity: {result['years_to_maturity']:.2f}")
    print(f"Implied annual yield: {result['implied_annual_yield']:.4%}")
    print(f"\nRecommended annual_index_growth_rate: {result['implied_annual_yield']:.6f}")
