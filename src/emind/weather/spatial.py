"""Diagnostic-only local ERA5 spatial sensitivity; no acceptance threshold.

Provider-validity hours are compared directly, including initial RAW SSRD.
The final dedicated boundary is excluded. All calculations use Python floats,
math.fsum and linear sample quantiles, matching the frozen C1 methodology.
"""
from collections import Counter
import json
import math
from pathlib import Path
import statistics
import subprocess

from .config import validate_weather_config
from .era5 import _digest, _provider_stamp, validate_grib
from .harmonise import canonical_index
from .structural import temporal_coverage, audit_plan


def stencil(config):
    box = config['audit_bbox']
    step = config['grid']['resolution_degrees']
    center = (config['anchor']['latitude'], config['anchor']['longitude'])
    grid = sorted((box['south'] + i * step, box['west'] + j * step) for i in range(3) for j in range(3))
    if center not in grid:
        raise ValueError('Central coordinate absent from stencil')
    return center, [point for point in grid if point != center]


def label(point):
    return f'{point[0]:.2f}N_{point[1]:.2f}E'


def quantile(values, q):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower = int(position)
    return ordered[lower] + (ordered[min(lower + 1, len(ordered) - 1)] - ordered[lower]) * (position - lower)


def mean(values):
    return math.fsum(values) / len(values)


def scalar_metrics(central, comparison):
    if len(central) != len(comparison):
        raise ValueError('Unequal scalar comparison lengths')
    n = len(central)
    if not n:
        return dict(count=0, pearson_correlation=None, mean_signed_difference=None,
                    MAE=None, RMSE=None, p95_absolute_difference=None,
                    p99_absolute_difference=None, maximum_absolute_difference=None)
    differences = [a-b for a,b in zip(central, comparison)]
    absolute = [abs(d) for d in differences]
    cm, nm = mean(central), mean(comparison)
    c = [v-cm for v in central]
    other = [v-nm for v in comparison]
    variance_c = math.fsum(v*v for v in c)
    variance_n = math.fsum(v*v for v in other)
    pearson = math.fsum(a*b for a,b in zip(c, other)) / math.sqrt(variance_c*variance_n) if variance_c and variance_n else None
    return {'count': n, 'pearson_correlation': pearson,
            'mean_signed_difference': mean(differences), 'MAE': mean(absolute),
            'RMSE': math.sqrt(mean([d*d for d in differences])),
            'p95_absolute_difference': quantile(absolute, .95),
            'p99_absolute_difference': quantile(absolute, .99), 'maximum_absolute_difference': max(absolute)}


def magnitude_summary(values):
    return {'count': len(values), 'mean': mean(values) if values else None,
            'median': statistics.median(values) if values else None,
            'p95': quantile(values, .95), 'p99': quantile(values, .99),
            'maximum': max(values) if values else None}


def rank_at(values, center):
    """Rank in ascending (value, latitude, longitude) order, including ties."""
    ordered = sorted((value, *point) for point, value in values.items())
    return next(i+1 for i, item in enumerate(ordered) if item[1:] == center)


def active_mask(central, neighbour_medians):
    return [c > 0 or n > 0 for c, n in zip(central, neighbour_medians)]


def wind_speed(u, v):
    return math.hypot(u, v)


def vector_error(uc, vc, un, vn):
    return math.hypot(uc-un, vc-vn)


def wind_from(u, v):
    return math.degrees(math.atan2(-u, -v)) % 360


def angular_difference(uc, vc, un, vn):
    """The frozen 1 m/s diagnostic eligibility rule is not acceptance logic."""
    if wind_speed(uc, vc) < 1.0 or wind_speed(un, vn) < 1.0:
        return None
    return abs(((wind_from(uc, vc) - wind_from(un, vn) + 180) % 360) - 180)


def season(month):
    return ('DJF' if month in (12, 1, 2) else 'MAM' if month in (3, 4, 5)
            else 'JJA' if month in (6, 7, 8) else 'SON')


def extreme_indices(discrepancies, timestamps, count=20):
    return sorted(range(len(discrepancies)), key=lambda i: (-discrepancies[i], timestamps[i]))[:count]


def _decode_grid(path):
    messages = json.loads(subprocess.check_output(
        ['grib_ls', '-j', '-p', 'shortName,validityDate,validityTime', str(path)], text=True))['messages']
    decoded = subprocess.check_output(['grib_get_data', '-F', '%.17g', str(path)], text=True)
    blocks = []
    for line in decoded.splitlines():
        if line.startswith('Latitude'):
            blocks.append([])
        elif line.strip():
            if not blocks:
                raise ValueError('GRIB decoded header absent')
            blocks[-1].append(tuple(map(float, line.split())))
    if len(blocks) != len(messages):
        raise ValueError('GRIB metadata/value message count differs')
    for m, rows in zip(messages, blocks):
        yield m['shortName'], _provider_stamp(m['validityDate'], m['validityTime']), rows


def read_stencil(raw_root, config, *, validated_inventory=None, progress=None):
    """Re-read monthly RAW. Default validation delegates all GRIB semantics to D3B.

    An explicit validated inventory may avoid redundant validation, while binding
    every decoded input to its previously validated content hash and byte size.
    """
    center, neighbours = stencil(config)
    coords = sorted([center] + neighbours)
    index = canonical_index(config)
    objects = audit_plan(raw_root, config, include_boundary=False)
    expected = None
    if validated_inventory:
        audit = json.loads(Path(validated_inventory).read_text())
        if audit['status'] != 'PASS':
            raise ValueError('Validated inventory is not PASS')
        entries = audit['inventory']
        expected = {str(Path(e['absolute_path']).resolve()): e for e in entries}
        if len(entries) != len(expected):
            raise ValueError('Duplicate validated inventory path')
    grids = {v['short_name']: {} for v in config['variables']}
    inputs = []
    for i, (path, year, month) in enumerate(objects):
        before = path.stat()
        sha = _digest(path)
        if expected is None:
            validate_grib(path, config, year=year, month=month)
        else:
            entry = expected.get(str(path.resolve()))
            if entry is None or entry['sha256'] != sha or entry['byte_size'] != before.st_size:
                raise ValueError('Spatial input validated checksum/size mismatch')
        count = 0
        for s, t, rows in _decode_grid(path):
            if s not in grids or t in grids[s]:
                raise ValueError('Unexpected variable or duplicate provider timestamp')
            if len(rows) != len(coords) or sorted((a,b) for a,b,_ in rows) != coords:
                raise ValueError('Malformed spatial grid coordinate set')
            if not all(math.isfinite(v) for _,_,v in rows) or (s == 'ssrd' and any(v < 0 for _,_,v in rows)):
                raise ValueError('Invalid spatial input values')
            mapped = {(a,b):v for a,b,v in rows}
            grids[s][t] = [mapped[p] for p in coords]
            count += 1
        after = path.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_ino) or _digest(path) != sha:
            raise ValueError('Spatial input changed during decoding')
        inputs.append({'path': str(path), 'sha256': sha, 'bytes': before.st_size, 'messages': count})
        if progress:
            progress(i+1, len(objects), path)
    columns = {}
    for s, values in grids.items():
        temporal_coverage(list(values), index)
        columns[s] = {p: [values[t][j] for t in index] for j,p in enumerate(coords)}
    return index, columns, inputs


def _scalar_group(series, center, neighbours, indices, ssrd=False):
    central = [series[center][i] for i in indices]
    neighbour_series = {label(p): [series[p][i] for i in indices] for p in neighbours}
    rows = list(zip(*neighbour_series.values()))
    median = [statistics.median(row) for row in rows]
    average = [mean(row) for row in rows]
    mask = active_mask(central, median) if ssrd else None
    comparisons = {}
    for name, other in {**neighbour_series, 'neighbour_median': median, 'neighbour_mean': average}.items():
        comparisons[name] = {'all_hours': scalar_metrics(central, other)}
        if ssrd:
            comparisons[name]['central_or_neighbour_median_positive'] = scalar_metrics(
                [v for v, yes in zip(central, mask) if yes], [v for v, yes in zip(other, mask) if yes])
    n = len(indices)
    below = sum(c < min(row) for c,row in zip(central, rows))
    above = sum(c > max(row) for c,row in zip(central, rows))
    counts = Counter(rank_at({p:series[p][i] for p in [center]+neighbours}, center) for i in indices)
    result = {'sample_count': n, 'comparisons': comparisons,
              'central_range_fractions': {'below_all_neighbours': below/n, 'above_all_neighbours': above/n,
                                           'inside_neighbour_range': (n-below-above)/n},
              'rank_counts': {str(i): counts[i] for i in range(1,10)},
              'rank_fractions': {str(i): counts[i]/n for i in range(1,10)}}
    if ssrd:
        sums = {name:math.fsum(values) for name, values in neighbour_series.items()}
        total = math.fsum(central)
        med = statistics.median(sums.values())
        result['summed_ssrd'] = {'units': 'J m**-2', 'central_sum': total, 'neighbour_sums': sums,
                'neighbour_median_of_sums': med,
                'central_relative_difference_from_neighbour_median_percent': 100*(total-med)/med if med else None,
                'minimum_neighbour_sum': min(sums.values()), 'maximum_neighbour_sum': max(sums.values()),
                'central_position': 'below' if total < min(sums.values()) else 'above' if total > max(sums.values()) else 'inside'}
    return result


def _vector_group(u, v, center, neighbours, indices):
    uc, vc = [u[center][i] for i in indices], [v[center][i] for i in indices]
    error, direction = {}, {}
    for p in neighbours:
        un, vn = [u[p][i] for i in indices], [v[p][i] for i in indices]
        error[label(p)] = magnitude_summary([vector_error(a,b,c,d) for a,b,c,d in zip(uc,vc,un,vn)])
        angles = [angular_difference(a,b,c,d) for a,b,c,d in zip(uc,vc,un,vn)]
        eligible = [angle for angle in angles if angle is not None]
        direction[label(p)] = {'eligible_timestamps': len(eligible), 'eligible_fraction': len(eligible)/len(indices),
              'median_absolute_angular_difference_degrees': statistics.median(eligible) if eligible else None,
              'p95_absolute_angular_difference_degrees': quantile(eligible,.95),
              'p99_absolute_angular_difference_degrees': quantile(eligible,.99)}
    um = [mean([u[p][i] for p in neighbours]) for i in indices]
    vm = [mean([v[p][i] for p in neighbours]) for i in indices]
    error['neighbour_mean_components'] = magnitude_summary([vector_error(a,b,c,d) for a,b,c,d in zip(uc,vc,um,vm)])
    return {'vector_error': error, 'direction': direction}


def spatial_diagnostics(index, columns, config, *, progress=None):
    """Pure scientific diagnostics on validated, ordered nine-point series."""
    center, neighbours = stencil(config)
    columns = dict(columns)
    for height in (10,100):
        u, v = columns[f'{height}u'], columns[f'{height}v']
        columns[f'wind_speed_{height}m'] = {p:[wind_speed(a,b) for a,b in zip(u[p],v[p])] for p in [center]+neighbours}
    groups = {'pooled': list(range(len(index))),
              'annual': {str(y):[i for i,t in enumerate(index) if t.year == y] for y in sorted({t.year for t in index})},
              'seasonal': {s:[i for i,t in enumerate(index) if season(t.month) == s] for s in ('DJF','MAM','JJA','SON')}}
    scalar, vectors = {}, {}
    units = {'K':'K', 'J/m2':'J m**-2', 'm/s':'m s**-1'}
    variable_units = {v['short_name']:units[v['unit']] for v in config['variables']}
    for number, (s, series) in enumerate(columns.items()):
        scalar[s] = {'units': variable_units.get(s,'m s**-1'),
                     'pooled': _scalar_group(series,center,neighbours,groups['pooled'],s=='ssrd')}
        for kind in ('annual','seasonal'):
            scalar[s][kind] = {}
            for name, indices in groups[kind].items():
                if not indices:
                    continue
                group = _scalar_group(series,center,neighbours,indices,s=='ssrd')
                if kind == 'seasonal':
                    group = {'sample_count':group['sample_count'],
                             'comparisons':{'neighbour_median':group['comparisons']['neighbour_median']}}
                scalar[s][kind][name] = group
        if progress:
            progress(number+1,len(columns),Path(s))
    for height in (10,100):
        u,v = columns[f'{height}u'], columns[f'{height}v']
        vectors[str(height)] = {'pooled': _vector_group(u,v,center,neighbours,groups['pooled'])}
        for kind in ('annual','seasonal'):
            vectors[str(height)][kind] = {}
            for name, indices in groups[kind].items():
                if not indices:
                    continue
                group = _vector_group(u,v,center,neighbours,indices)
                if kind == 'seasonal':
                    group = {'vector_error':{'neighbour_mean_components':group['vector_error']['neighbour_mean_components']},
                             'direction':{}}
                vectors[str(height)][kind][name] = group
    extremes = []
    for s in ('2t','ssrd'):
        series = columns[s]
        medians = [statistics.median([series[p][i] for p in neighbours]) for i in range(len(index))]
        delta = [abs(c-n) for c,n in zip(series[center],medians)]
        for i in extreme_indices(delta,index):
            t=index[i]
            extremes.append({'diagnostic':s+'_absolute_difference_to_neighbour_median',
                'timestamp':t.strftime('%Y-%m-%dT%H:%M:%SZ'), 'year':t.year,'month':t.month,
                'central_value':series[center][i], 'neighbour_values':{label(p):series[p][i] for p in neighbours},
                'neighbour_median':medians[i],'discrepancy':delta[i],'units':variable_units[s]})
    for height in (10,100):
        u,v=columns[f'{height}u'],columns[f'{height}v']
        um=[mean([u[p][i] for p in neighbours]) for i in range(len(index))]
        vm=[mean([v[p][i] for p in neighbours]) for i in range(len(index))]
        delta=[vector_error(a,b,c,d) for a,b,c,d in zip(u[center],v[center],um,vm)]
        for i in extreme_indices(delta,index):
            t=index[i]
            extremes.append({'diagnostic':f'{height}m_vector_error_to_neighbour_mean',
                'timestamp':t.strftime('%Y-%m-%dT%H:%M:%SZ'),'year':t.year,'month':t.month,
                'central_u':u[center][i],'central_v':v[center][i], 'neighbour_mean_u':um[i],'neighbour_mean_v':vm[i],
                'neighbour_values':{label(p):{'u':u[p][i],'v':v[p][i]} for p in neighbours},
                'discrepancy':delta[i],'units':'m s**-1'})
    headline = {}
    for s in ('2t','ssrd','wind_speed_10m','wind_speed_100m'):
        item = scalar[s]
        headline[s] = {'pooled_vs_neighbour_median': item['pooled']['comparisons']['neighbour_median']['all_hours'],
                      'annual_MAE': {k:v['comparisons']['neighbour_median']['all_hours']['MAE'] for k,v in item['annual'].items()},
                      'seasonal_MAE': {k:v['comparisons']['neighbour_median']['all_hours']['MAE'] for k,v in item['seasonal'].items()}}
    headline['ssrd'].update(active_hours_vs_neighbour_median=scalar['ssrd']['pooled']['comparisons']['neighbour_median']['central_or_neighbour_median_positive'],
                            summed_ssrd=scalar['ssrd']['pooled']['summed_ssrd'])
    headline['wind_vector_error'] = {h:d['pooled']['vector_error']['neighbour_mean_components'] for h,d in vectors.items()}
    headline['central_range_fractions'] = {s:d['pooled']['central_range_fractions'] for s,d in scalar.items()}
    headline['largest_discrepancies'] = [extremes[i] for i in range(0,len(extremes),20)]
    return {'scalar_diagnostics':scalar, 'wind_vector_and_direction_diagnostics':vectors,
            'extreme_discrepancies':extremes, 'headline_numbers':headline}


def spatial_audit(raw_root, config, *, validated_inventory=None, progress=None):
    config = validate_weather_config(config)
    index, columns, inputs = read_stencil(raw_root,config,validated_inventory=validated_inventory,progress=progress)
    center,neighbours = stencil(config)
    result = spatial_diagnostics(index,columns,config,progress=progress)
    result.update(execution_status='PASS',status_definition='PASS means complete consistent execution only; no scientific acceptance threshold.',
        input_objects=inputs,input_object_count=len(inputs),timestamp_count=len(index),central_coordinate=center,
        neighbour_coordinates=neighbours, diagnostic_speed_threshold_m_s=1.0,
        tie_policy='Ascending (value, latitude, longitude); rank 1 lowest, 9 highest; strict range inequalities.',
        wind_direction_convention='Meteorological wind-from degrees(atan2(-u,-v)) modulo 360',
        horizon={'first_provider_validity':config['horizon']['start'],'last_provider_validity':config['horizon']['end_inclusive'],
                 'ssrd_uses_common_provider_validity_horizon':True,'2026_boundary_included':False,
                 'ssrd_initial_pre_horizon_interval_retained_in_common_comparison':True},
        quality_checks={'objects':len(inputs),'timestamps_each':len(index),'central_exists_every_timestamp':True,
                        'neighbours_each_timestamp':len(neighbours),'missing_input_values':0,'non_finite_input_values':0,'2026_boundary_used':False},
        transformations=False,diagnostic_calculations_in_memory_only=True,canonical_data_created=False)
    return result


def compare_spatial(actual, reference, *, rel_tol=1e-12, abs_tol=1e-12):
    """Numerical regression tolerance only; never scientific acceptance criteria."""
    checked, maximum = 0, 0.0
    def compare(a,b,path):
        nonlocal checked, maximum
        if isinstance(b,dict):
            if not isinstance(a,dict) or a.keys() != b.keys():
                raise ValueError('C1 regression field mismatch: '+path)
            for key in b:
                compare(a[key],b[key],path+'.'+key)
        elif isinstance(b,list):
            if len(a) != len(b):
                raise ValueError('C1 regression length mismatch: '+path)
            for i,(x,y) in enumerate(zip(a,b)):
                compare(x,y,path+f'[{i}]')
        elif type(b) is float:
            if not math.isfinite(a) or not math.isclose(a,b,rel_tol=rel_tol,abs_tol=abs_tol):
                raise ValueError('C1 numeric regression mismatch: '+path)
            checked += 1
            maximum = max(maximum,abs(a-b))
        elif a != b:
            raise ValueError('C1 regression mismatch: '+path)
    for key in ('scalar_diagnostics','wind_vector_and_direction_diagnostics','extreme_discrepancies','headline_numbers'):
        compare(actual[key],reference[key],key)
    for key in ('input_object_count','timestamp_count','horizon'):
        compare(actual[key],reference[key],key)
    return {'status':'PASS','numeric_fields_compared':checked,'maximum_absolute_difference':maximum,
            'relative_tolerance':rel_tol,'absolute_tolerance':abs_tol,'detailed_metrics_and_extremes_match':True}
