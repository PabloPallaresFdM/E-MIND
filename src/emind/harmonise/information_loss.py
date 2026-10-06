"""Descriptive aggregation diagnostics; no fitted transform or causal claim."""
from decimal import Decimal, localcontext
import statistics


def quantile(values, probability):
    """Linear interpolation at (n-1)*p (R/type-7 convention)."""
    ordered = sorted(values)
    p = Decimal(str(probability))
    if not ordered or not 0 <= p <= 1:
        raise ValueError('Invalid quantile input')
    position = (len(ordered) - 1) * p
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def power_statistics(values):
    values = list(values)
    if not values or any(not v.is_finite() for v in values):
        raise ValueError('Empty/nonfinite power series')
    mean = sum(values, Decimal(0)) / len(values)
    variance = statistics.pvariance(values)
    return {'count': len(values), 'min_mw': min(values), 'max_mw': max(values),
            'mean_mw': mean, 'median_mw': statistics.median(values),
            'coefficient_of_variation': variance.sqrt()/mean if mean else None,
            'variance_mw2_population': variance, 'stddev_mw_population': variance.sqrt(),
            'p95_mw': quantile(values, '.95'), 'p99_mw': quantile(values, '.99')}


def ramps(values, interval_hours):
    if interval_hours <= 0 or len(values) < 2:
        raise ValueError('Invalid ramp input')
    differences = [b-a for a,b in zip(values, values[1:])]
    absolute = [abs(v) for v in differences]
    return {'interval_hours': interval_hours, 'count': len(differences),
            'signed_delta_mw_min': min(differences), 'signed_delta_mw_max': max(differences),
            'signed_rate_mw_per_hour_min': min(differences)/interval_hours,
            'signed_rate_mw_per_hour_max': max(differences)/interval_hours,
            'absolute_delta_mw': {'median': statistics.median(absolute), 'p95': quantile(absolute,'.95'), 'p99': quantile(absolute,'.99'), 'max': max(absolute)},
            'absolute_rate_mw_per_hour': {'median': statistics.median(absolute)/interval_hours, 'p95': quantile(absolute,'.95') / interval_hours,
                                          'p99': quantile(absolute,'.99') / interval_hours,
                                          'max': max(absolute) / interval_hours}}


def information_loss(native_energy, hourly_power):
    native_energy, hourly_power = list(native_energy), list(hourly_power)
    if len(native_energy) != 4 * len(hourly_power):
        raise ValueError('Selected native and hourly lengths disagree')
    with localcontext() as context:
        context.prec = 40
        native_power = [e / Decimal('.25') for e in native_energy]
        native = power_statistics(native_power)
        hourly = power_statistics(hourly_power)
        reduction = native['max_mw'] - hourly['max_mw']
        return {'description': 'Population moments at differing aggregation frequencies; descriptive, not causal.',
                'quantiles': 'Linear interpolation at (n-1)*p; type 7',
                'native_15min': native, 'hourly': hourly,
                'peak_reduction_mw': reduction,
                'peak_reduction_fraction': reduction/native['max_mw'] if native['max_mw'] else None,
                'native_ramps': ramps(native_power, Decimal('.25')),
                'hourly_ramps': ramps(hourly_power, Decimal(1)),
                'ramp_caution': 'Delta MW intervals differ. MW/h normalises time but retains different smoothing horizons.'}


def distribution(values):
    return {'median': statistics.median(values), 'p95': quantile(values, '.95'),
            'p99': quantile(values, '.99'), 'max': max(values)}


def detailed_information_loss(native_energy, hourly_power, hour_starts):
    """Exact aligned population, UTC years; year ramps exclude cross-year pairs."""
    from datetime import timedelta
    from emind.harmonise.coverage import validate_index
    native_energy, hourly_power, hour_starts = list(native_energy), list(hourly_power), list(hour_starts)
    validate_index(hour_starts)
    if len(hour_starts) != len(hourly_power) or len(native_energy) != 4*len(hourly_power):
        raise ValueError('Population cardinality mismatch')
    if any(sum(native_energy[i*4:i*4+4], Decimal(0)) != value for i,value in enumerate(hourly_power)):
        raise ValueError('Per-hour native energy does not match canonical hourly energy/power')
    with localcontext() as context:
        context.prec = 40
        result = information_loss(native_energy, hourly_power)
        native_total = sum(native_energy, Decimal(0))
        hourly_total = sum(hourly_power, Decimal(0))  # each value spans exactly 1 h
        result['energy_check'] = {'native_mwh': native_total, 'hourly_mwh': hourly_total,
                                  'absolute_difference_mwh': abs(hourly_total-native_total),
                                  'relative_difference': abs(hourly_total-native_total)/native_total if native_total else None,
                                  'tolerance_mwh': Decimal(0)}
        power = [v / Decimal('.25') for v in native_energy]
        groups = [power[i:i+4] for i in range(0,len(power),4)]
        hidden = [max(g)-v for g,v in zip(groups,hourly_power)]
        ranges = [max(g)-min(g) for g in groups]
        stds = [statistics.pvariance(g).sqrt() for g in groups]
        result['hidden_peak_mw'] = distribution(hidden)
        result['within_hour_range_mw'] = distribution(ranges)
        result['within_hour_std_mw_population'] = distribution(stds)
        peak = max(power)
        peak_index = power.index(peak)  # earliest physical instant if tied
        hour_index = peak_index//4
        attenuation = peak-hourly_power[hour_index]
        result['global_native_peak'] = {
            'timestamp_utc': hour_starts[hour_index]+timedelta(minutes=15*(peak_index%4)),
            'native_power_mw': peak, 'containing_hour_utc': hour_starts[hour_index],
            'hourly_power_mw': hourly_power[hour_index], 'attenuation_mw': attenuation,
            'attenuation_fraction': attenuation/peak if peak else None,
            'tie_count':power.count(peak), 'tie_rule':'earliest UTC instant'}
        result['relative_change_convention'] = '(hourly - native) / native; negative means reduction. Peak reduction uses (native_max - hourly_max) / native_max.'
        for name in ('stddev_mw_population','variance_mw2_population'):
            baseline=result['native_15min'][name]
            result[name+'_relative_change']=(result['hourly'][name]-baseline)/baseline if baseline else None
        # Predeclared, not chosen after observing outcomes. Denominator is each
        # hour's native peak, distinct from the global-maximum comparison.
        result['attenuation_thresholds'] = {'denominator':'native within-hour peak',
            'strictly_greater_fraction_counts': {str(t):sum((max(g)-v)/max(g)>t for g,v in zip(groups,hourly_power) if max(g)>0)
                                               for t in (Decimal('.01'),Decimal('.05'),Decimal('.10'))}}
        annual={}
        for year in sorted({t.year for t in hour_starts}):
            indices=[i for i,t in enumerate(hour_starts) if t.year==year]
            first,last=indices[0],indices[-1]+1
            yearly_native=native_energy[4*first:4*last]
            yearly_hourly=hourly_power[first:last]
            a=information_loss(yearly_native,yearly_hourly)
            annual[str(year)]={'hourly_intervals':len(yearly_hourly),'native_intervals':len(yearly_native),
                'native_max_mw':a['native_15min']['max_mw'],'hourly_max_mw':a['hourly']['max_mw'],
                'peak_reduction_fraction':a['peak_reduction_fraction'],
                'native_std_mw':a['native_15min']['stddev_mw_population'],
                'hourly_std_mw':a['hourly']['stddev_mw_population'],
                'within_hour_range_p99_mw':quantile(ranges[first:last],'.99'),
                'native_abs_ramp_max_mw_per_h':a['native_ramps']['absolute_rate_mw_per_hour']['max'],
                'hourly_abs_ramp_max_mw_per_h':a['hourly_ramps']['absolute_rate_mw_per_hour']['max']}
        result['annual_utc']=annual
        result['annual_ramp_boundary_rule']='Only consecutive pairs within each UTC calendar year; full-period ramps include cross-year pairs.'
        return result
