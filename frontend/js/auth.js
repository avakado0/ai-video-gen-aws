// Cognito hosted UI, authorization code + PKCE, public client. Tokens live in sessionStorage.

const KEY = 'auth';

const b64url = (buf) =>
  btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');

const randomString = (bytes = 32) => b64url(crypto.getRandomValues(new Uint8Array(bytes)));

export class Auth {
  /** @param {{domain: string, clientId: string, redirectUri: string}} cfg */
  constructor(cfg) { this.cfg = cfg; }

  #read() {
    try { return JSON.parse(sessionStorage.getItem(KEY)); } catch { return null; }
  }

  #write(tokens) {
    sessionStorage.setItem(KEY, JSON.stringify({ ...tokens, expiresAt: Date.now() + tokens.expires_in * 1000 }));
  }

  async login() {
    const verifier = randomString(64);
    const challenge = b64url(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier)));
    sessionStorage.setItem('pkce_verifier', verifier);
    const q = new URLSearchParams({
      response_type: 'code', client_id: this.cfg.clientId, redirect_uri: this.cfg.redirectUri,
      scope: 'openid email', code_challenge_method: 'S256', code_challenge: challenge,
    });
    location.href = `https://${this.cfg.domain}/oauth2/authorize?${q}`;
  }

  /** Finish the redirect if the URL carries ?code=. */
  async handleRedirect() {
    const params = new URLSearchParams(location.search);
    const code = params.get('code');
    if (!code) return;
    const res = await fetch(`https://${this.cfg.domain}/oauth2/token`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        grant_type: 'authorization_code', client_id: this.cfg.clientId, code,
        redirect_uri: this.cfg.redirectUri, code_verifier: sessionStorage.getItem('pkce_verifier') ?? '',
      }),
    });
    if (!res.ok) throw new Error('Login failed');
    this.#write(await res.json());
    sessionStorage.removeItem('pkce_verifier');
    history.replaceState({}, '', location.pathname);
  }

  /** @returns {string|null} id token while valid */
  token() {
    const t = this.#read();
    return t && t.expiresAt > Date.now() ? t.id_token : null;
  }

  user() {
    const tok = this.token();
    if (!tok) return null;
    const claims = JSON.parse(atob(tok.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')));
    return { name: claims.email ?? claims['cognito:username'] ?? claims.sub };
  }

  logout() {
    sessionStorage.removeItem(KEY);
    const q = new URLSearchParams({ client_id: this.cfg.clientId, logout_uri: this.cfg.redirectUri });
    location.href = `https://${this.cfg.domain}/logout?${q}`;
  }
}
