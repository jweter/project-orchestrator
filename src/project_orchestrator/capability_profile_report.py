def summarize_capability_profile(profile):
    result = {
        'model': profile.get('model', None),
        'max_passing_level': profile.get('max_passing_level', 0),
        'levels': []
    }

    if 'levels' in profile:
        levels = profile['levels']
        for level_str, level_data in levels.items():
            level_num = int(level_str)
            pass_rate = level_data.get('pass_rate', None)
            median_wall_s = level_data.get('median_wall_s', None)
            status = level_data.get('status', None)
            result['levels'].append({
                'level': level_num,
                'pass_rate': pass_rate,
                'median_wall_s': median_wall_s,
                'status': status
            })
        result['levels'].sort(key=lambda x: x['level'])

    return result
