/**
 * Passkeys / WebAuthn (M5.3): zero-email re-auth. After one magic-link
 * sign-in the user can enroll a platform passkey (Face ID /
 * fingerprint / device PIN); subsequent sign-ins on that device never
 * touch the email service.
 *
 * Uses the JSON serialization API (parseCreationOptionsFromJSON /
 * parseRequestOptionsFromJSON / toJSON) -- universal in 2026 browsers;
 * anything older simply doesn't get the passkey buttons and keeps the
 * email flow.
 */

type PKCStatic = typeof PublicKeyCredential & {
  parseCreationOptionsFromJSON?: (json: unknown) => CredentialCreationOptions["publicKey"];
  parseRequestOptionsFromJSON?: (json: unknown) => CredentialRequestOptions["publicKey"];
};

export function passkeysSupported(): boolean {
  const pkc = window.PublicKeyCredential as PKCStatic | undefined;
  return !!pkc &&
    typeof pkc.parseCreationOptionsFromJSON === "function" &&
    typeof pkc.parseRequestOptionsFromJSON === "function";
}

export type PasskeyResult = "ok" | "cancel" | "error";

/** Usernameless sign-in with a discoverable credential. */
export async function signInWithPasskey(): Promise<PasskeyResult> {
  let handle: string;
  let options: unknown;
  try {
    const r = await fetch("/api/auth/webauthn/auth-options", { method: "POST" });
    if (!r.ok) return "error";
    ({ handle, options } = (await r.json()) as { handle: string; options: unknown });
  } catch {
    return "error";
  }
  let cred: PublicKeyCredential | null;
  try {
    const pkc = window.PublicKeyCredential as PKCStatic;
    cred = (await navigator.credentials.get({
      publicKey: pkc.parseRequestOptionsFromJSON!(options),
    })) as PublicKeyCredential | null;
  } catch (e) {
    // The user closing the platform sheet raises NotAllowedError --
    // that's a choice, not a failure worth a toast.
    return (e as DOMException)?.name === "NotAllowedError" ? "cancel" : "error";
  }
  if (!cred) return "cancel";
  try {
    const r = await fetch("/api/auth/webauthn/authenticate", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        handle,
        credential: (cred as PublicKeyCredential & { toJSON(): unknown }).toJSON(),
      }),
    });
    return r.ok ? "ok" : "error";
  } catch {
    return "error";
  }
}

/** Enroll a passkey on the signed-in account. */
export async function registerPasskey(): Promise<PasskeyResult> {
  let options: unknown;
  try {
    const r = await fetch("/api/auth/webauthn/register-options", { method: "POST" });
    if (!r.ok) return "error";
    options = await r.json();
  } catch {
    return "error";
  }
  let cred: PublicKeyCredential | null;
  try {
    const pkc = window.PublicKeyCredential as PKCStatic;
    cred = (await navigator.credentials.create({
      publicKey: pkc.parseCreationOptionsFromJSON!(options),
    })) as PublicKeyCredential | null;
  } catch (e) {
    return (e as DOMException)?.name === "NotAllowedError" ? "cancel" : "error";
  }
  if (!cred) return "cancel";
  try {
    const r = await fetch("/api/auth/webauthn/register", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        credential: (cred as PublicKeyCredential & { toJSON(): unknown }).toJSON(),
      }),
    });
    return r.ok ? "ok" : "error";
  } catch {
    return "error";
  }
}
