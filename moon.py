"""Moon's login-code protocol, with an independent in-memory session."""
import json
import re
import time

PUBLIC_API_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpYXQiOjE3NzYwMzkzNzYsImV4cCI6MTg5MzQ1NjAwMCwicm9sZSI6ImFub24iLCJpc3MiOiJzdXBhYmFzZSJ9.f_-K38u3odjltP-g_67FVmG32Vg-_-k-lNBvIaVUVBM'  # Provider's public Supabase client key, not a user credential.

class Moon:
    def __init__(self, transport, store=None):
        self.transport = transport
        self.store = store
        self.session = None

    def _post(self, url, data, headers=None):
        stage = 'Discord code redemption' if '/code/redeem' in url else 'Moon session verification' if '/verify' in url else 'Moon session refresh'
        try:
            return json.loads(self.transport.request(url, {'Content-Type':'application/json', **(headers or {})}, data))
        except RuntimeError as error:
            raise RuntimeError(f'{stage}: {error}') from None

    def _accept(self, session):
        if not all(isinstance(session.get(key), str) and session[key] and not re.search(r'[\r\n\0]', session[key]) for key in ('access_token', 'refresh_token')):
            raise RuntimeError('Moon returned an invalid session.')
        session['expires_at'] = time.time() + int(session.get('expires_in', 3600))
        if self.store is not None:
            self.store.save(session)
        self.session = session
        return session['access_token']

    def login(self, code):
        code = code.strip().upper()
        if not re.fullmatch(r'[A-Z0-9]{6}', code):
            raise ValueError('Enter the six-character login code provided by lua.tools.')
        result = self._post('https://lua.tools/api/auth/code/redeem', {'code':code})
        if not isinstance(result.get('token'), str) or not result['token']:
            raise RuntimeError('Moon returned an invalid login response.')
        self._accept(self._post('https://db.lua.tools/auth/v1/verify', {'type':'magiclink', 'token_hash':result['token']}, {'apikey':PUBLIC_API_KEY}))
        return 'Signed into Moon. Steam authentication is separate.'

    def token(self):
        if self.session is None and self.store is not None:
            session = self.store.read()
            if session is not None:
                if not isinstance(session, dict) or not all(isinstance(session.get(key), str) and session[key] and not re.search(r'[\r\n\0]', session[key]) for key in ('access_token', 'refresh_token')):
                    raise RuntimeError('Saved Moon session is invalid. Sign out and sign in again.')
                try: session['expires_at'] = float(session.get('expires_at', 0))
                except (TypeError, ValueError): session['expires_at'] = 0
                self.session = session
        if self.session is None:
            raise ValueError('Sign into Moon using a lua.tools login code first.')
        if self.session['expires_at'] <= time.time() + 60:
            session = self._post('https://db.lua.tools/auth/v1/token?grant_type=refresh_token', {'refresh_token':self.session['refresh_token']}, {'apikey':PUBLIC_API_KEY})
            self._accept(session)
        return self.session['access_token']

    def logout(self):
        if self.store is not None:
            self.store.clear()
        self.session = None
        return 'Signed out of Moon. Saved session removed from KWallet.'
