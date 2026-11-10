export type AuthUser = {
  subject: string; email: string; username: string;
  display_name: string | null; avatar_url: string | null;
  locale: "ko" | "en"; time_zone: string | null;
  is_active: boolean; registration_status: "approved"; is_global_admin: boolean;
  is_email_verified: boolean; email_verification_required: boolean;
  created_at: number; updated_at: number; last_login_at: number | null;
  has_password: boolean; connected_providers: string[];
};
export type AuthConfig = {
  email_verification_required: boolean; password_min_length: number;
  public_registration_enabled: boolean; public_registration_policy: "open" | "closed";
  google_oauth_enabled: boolean; google_client_id: string | null;
};
export type AuthSessionResponse = {
  access_token: string; token_type: string; expires_in: number; user: AuthUser;
};
export type AuthMessage = { message: string; token?: string | null };
export type AccountSession = {
  id: string; created_at: number; last_used_at: number | null;
  expires_at: number; ip_address: string | null; user_agent: string | null; is_current: boolean;
};
