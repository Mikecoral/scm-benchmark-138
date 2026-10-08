from __future__ import annotations

import json
import os
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from paper_search.benchmark.schema import SYSTEM_PROMPT, DISCUSSION_SYSTEM_PROMPT, render, strict_json

RESERVED = {'model', 'messages', 'stream', 'stream_options', 'max_tokens', 'temperature', 'response_format', 'n'}


def output_protocol(profile):
    extra=profile.get('extra_body',{})
    disabled=(extra.get('enable_thinking') is False or
              isinstance(extra.get('chat_template_kwargs'),dict) and extra['chat_template_kwargs'].get('enable_thinking') is False or
              isinstance(extra.get('thinking'),dict) and extra['thinking'].get('type')=='disabled' or
              extra.get('reasoning_effort')=='none')
    return 'discussion-answer-json-v2' if disabled else 'answer-json-v1'


def system_prompt(profile):
    base = DISCUSSION_SYSTEM_PROMPT if output_protocol(profile)=='discussion-answer-json-v2' else SYSTEM_PROMPT
    suffix = profile.get('system_prompt_suffix', '')
    return base + '\n\n' + suffix if suffix else base


def validate_profile(profile):
    required = {'provider', 'base_url', 'model', 'api_key_env', 'max_tokens', 'timeout_seconds', 'stream', 'retries', 'extra_body'}
    allowed = required | {'temperature', 'json_mode', 'retry_delay_seconds', 'concurrency', 'system_prompt_suffix'}
    if set(profile) - allowed or required - set(profile):
        raise ValueError('invalid provider profile fields')
    if profile['provider'] not in {'deepseek', 'qwen', 'openai_compatible'}:
        raise ValueError('unknown provider')
    if type(profile.get('concurrency', 4)) is not int or profile.get('concurrency', 4) < 1:
        raise ValueError('concurrency must be a positive integer')
    if 'system_prompt_suffix' in profile and (not isinstance(profile['system_prompt_suffix'], str) or not profile['system_prompt_suffix'].strip()):
        raise ValueError('system_prompt_suffix must be a nonempty string')
    url = urlsplit(profile['base_url'])
    if url.scheme not in {'https', 'http'} or not url.netloc or url.username or url.password or url.query or url.fragment:
        raise ValueError('base_url must be a plain HTTP(S) API base URL')
    if url.scheme == 'http' and url.hostname not in {'127.0.0.1', 'localhost', '::1'}:
        raise ValueError('use HTTPS for remote API calls')
    if not all(isinstance(profile[k], str) and profile[k] for k in ['model', 'api_key_env']):
        raise ValueError('model and api_key_env required')
    for k in ['max_tokens', 'retries']:
        if type(profile[k]) is not int or profile[k] < (1 if k == 'max_tokens' else 0):
            raise ValueError('invalid ' + k)
    if type(profile['timeout_seconds']) not in (int, float) or profile['timeout_seconds'] <= 0:
        raise ValueError('timeout must be positive')
    if type(profile['stream']) is not bool or type(profile.get('json_mode', True)) is not bool:
        raise ValueError('stream/json_mode must be boolean')
    if 'temperature' in profile and profile['temperature'] is not None and (type(profile['temperature']) not in (int, float) or not 0 <= profile['temperature'] <= 2):
        raise ValueError('temperature must be null or 0–2')
    if type(profile.get('retry_delay_seconds', 1)) not in (int, float) or profile.get('retry_delay_seconds', 1) < 0:
        raise ValueError('invalid retry delay')
    extra = profile['extra_body']
    if not isinstance(extra, dict) or RESERVED & set(extra):
        raise ValueError('extra_body cannot override request identity/messages/protocol')
    if any('key' in k.lower() or 'authorization' in k.lower() or 'token' == k.lower() for k in extra):
        raise ValueError('credentials must be environment variables, not extra_body')
    if extra.get('enable_search') or 'tools' in extra:
        raise ValueError('benchmark evaluation must not enable external search/tools')
    return profile


def request_body(item, profile):
    validate_profile(profile)
    body = dict(model=profile['model'], messages=[dict(role='system', content=system_prompt(profile)),
                                               dict(role='user', content=render(item))],
                max_tokens=profile['max_tokens'], stream=profile['stream'])
    if profile.get('temperature') is not None:
        body['temperature'] = profile['temperature']
    # A discussion preceding JSON is incompatible with whole-response JSON mode.
    if profile.get('json_mode', True) and output_protocol(profile)=='answer-json-v1':
        body['response_format'] = {'type': 'json_object'}
    if profile['stream']:
        body['stream_options'] = {'include_usage': True}
    body.update(profile['extra_body'])
    return body


def _reasoning_text(message):
    # vLLM 0.31 uses reasoning; older servers use reasoning_content.
    for field in ('reasoning_content', 'reasoning'):
        value = message.get(field)
        if isinstance(value, str) and value:
            return value
    return ''


def _sse(stream, record):
    content, reasoning = [], []
    event_lines = []
    def consume():
        if not event_lines:
            return
        text = '\n'.join(event_lines)
        event_lines.clear()
        if text == '[DONE]':
            record['stream_done'] = True
            return
        value = strict_json(text)
        if value.get('error'):
            raise ValueError('provider returned streaming error')
        record['response_id'] = value.get('id', record.get('response_id'))
        record['actual_model'] = value.get('model', record.get('actual_model'))
        if value.get('usage'):
            record['usage'] = value['usage']
        for choice in value.get('choices', []):
            if choice.get('index', 0) != 0:
                continue
            delta = choice.get('delta', {})
            if isinstance(delta.get('content'), str):
                content.append(delta['content'])
            reasoning.append(_reasoning_text(delta))
            if choice.get('finish_reason') is not None:
                record['finish_reason'] = choice['finish_reason']
    try:
        for line in stream:
            text = line.decode('utf-8')
            if not text.strip():
                consume()
            elif text.startswith('data:'):
                event_lines.append(text[5:].strip())
        consume()
    finally:
        record.update(content=''.join(content), reasoning_content=''.join(reasoning))


def call_once(item, profile, api_key):
    body = request_body(item, profile)
    record = dict(id=item['id'], request_body=body, finish_reason=None)
    started = time.monotonic()
    request = Request(profile['base_url'].rstrip('/') + '/chat/completions',
                      data=json.dumps(body, ensure_ascii=False).encode(),
                      headers={'Authorization': 'Bearer ' + api_key, 'Content-Type': 'application/json'})
    context = ssl.create_default_context()
    if os.path.exists('/etc/ssl/cert.pem'):
        context.load_verify_locations('/etc/ssl/cert.pem')
    try:
        with urlopen(request, timeout=profile['timeout_seconds'], context=context) as response:
            record['http_status'] = response.status
            if profile['stream']:
                _sse(response, record)
            else:
                raw_text = response.read().decode('utf-8')
                raw = strict_json(raw_text)
                choice = raw['choices'][0]
                record.update(content=choice['message'].get('content'),
                              reasoning_content=_reasoning_text(choice['message']),
                              finish_reason=choice.get('finish_reason'), usage=raw.get('usage'),
                              response_id=raw.get('id'), actual_model=raw.get('model'))
    except HTTPError as exc:
        record.update(error='HTTPError', http_status=exc.code,
                      raw_error_body=exc.read().decode('utf-8', errors='replace'))
    except (URLError, TimeoutError, OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        record.update(error=type(exc).__name__, error_message=str(exc))
    record['elapsed_seconds'] = round(time.monotonic()-started, 6)
    # Even an upstream echo/error must not persist the credential.
    def redact(v):
        if isinstance(v, str):
            return v.replace(api_key, '[REDACTED]')
        if isinstance(v, list):
            return [redact(x) for x in v]
        if isinstance(v, dict):
            return {k: redact(x) for k, x in v.items()}
        return v
    return redact(record)
