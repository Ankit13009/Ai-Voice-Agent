/**
 * API contract types.
 *
 * These mirror the backend exactly: the envelope from `app/core/response.py`,
 * the error codes from `app/core/errors.py`, and the entity shapes from
 * `app/schemas/`. Nothing in the app should type an API response inline; import
 * from here so a backend change surfaces as a compile error rather than as
 * `undefined` at runtime.
 *
 * The envelope is a discriminated union on `success`. TypeScript narrows on it,
 * so `data` is unreachable until a response is known to be a success, and
 * `error` unreachable until it is known to be a failure.
 */

// --------------------------------------------------------------------------- //
// Envelope
// --------------------------------------------------------------------------- //
export interface PaginationMeta {
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
  has_next: boolean;
  has_prev: boolean;
}

export interface ApiFieldError {
  /** Dotted path to the offending input, e.g. "customer_phone". */
  field: string;
  message: string;
}

export interface ApiErrorBody {
  code: ApiErrorCode;
  message: string;
  details: ApiFieldError[];
}

export interface ApiSuccessResponse<T> {
  success: true;
  data: T;
  meta: PaginationMeta | null;
  message: string | null;
  request_id: string;
  timestamp: string;
}

export interface ApiErrorResponse {
  success: false;
  error: ApiErrorBody;
  request_id: string;
  timestamp: string;
}

export type ApiResponse<T> = ApiSuccessResponse<T> | ApiErrorResponse;

/**
 * Stable error codes. Kept as a union rather than an enum so a code the backend
 * adds later still type-checks as a string at the boundary, while the known
 * ones remain autocompletable.
 */
export type ApiErrorCode =
  | "VALIDATION_ERROR"
  | "BAD_REQUEST"
  | "UNAUTHENTICATED"
  | "INVALID_CREDENTIALS"
  | "TOKEN_EXPIRED"
  | "TOKEN_INVALID"
  | "FORBIDDEN"
  | "INSUFFICIENT_ROLE"
  | "CLINIC_ACCESS_DENIED"
  | "WEBHOOK_SIGNATURE_INVALID"
  | "NOT_FOUND"
  | "CONFLICT"
  | "ALREADY_EXISTS"
  | "SLOT_UNAVAILABLE"
  | "UNPROCESSABLE"
  | "RATE_LIMITED"
  | "INTERNAL_ERROR"
  | "UPSTREAM_ERROR"
  | "INTEGRATION_NOT_CONFIGURED"
  | "SERVICE_UNAVAILABLE"
  | (string & {});

// --------------------------------------------------------------------------- //
// Enums (mirror app/db/models.py)
// --------------------------------------------------------------------------- //
export type UserRole = "superadmin" | "owner" | "staff";

export type Language = "hi" | "en" | "hi-en";

export type CallOutcome =
  | "booked"
  | "rescheduled"
  | "cancelled"
  | "enquiry"
  | "no_details"
  | "failed";

export type AppointmentStatus =
  | "scheduled"
  | "rescheduled"
  | "cancelled"
  | "completed"
  | "no_show";

export type MessageKind =
  | "confirmation"
  | "reminder_24h"
  | "reminder_2h"
  | "cancellation"
  | "reschedule";

export type MessageStatus =
  | "pending"
  | "sent"
  | "delivered"
  | "read"
  | "failed"
  | "cancelled";

// --------------------------------------------------------------------------- //
// Auth
// --------------------------------------------------------------------------- //
export interface User {
  id: string;
  email: string;
  full_name: string;
  role: UserRole;
  business_id: string | null;
  last_login_at: string | null;
  must_change_password?: boolean;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
  /** Access token lifetime in seconds. */
  expires_in: number;
}

export interface LoginPayload {
  user: User;
  tokens: TokenPair;
}

export interface LoginRequest {
  email: string;
  password: string;
}

export interface ChangePasswordRequest {
  current_password: string;
  new_password: string;
}

// --------------------------------------------------------------------------- //
// Business
// --------------------------------------------------------------------------- //
export interface StaffMember {
  id: string;
  name: string;
  specialization: string;
  google_calendar_id: string;
  consultation_duration_minutes: number;
  is_active: boolean;
}

export interface BusinessUser {
  id: string;
  email: string;
  full_name: string;
  role: UserRole;
  is_active: boolean;
  last_login_at: string | null;
  /** True while a lockout from repeated failed logins is still in force. */
  is_locked: boolean;
  must_change_password: boolean;
}

/** Returned once, on create or reset. The password is not recoverable after this. */
export interface TemporaryPassword {
  id: string;
  email: string;
  temporary_password: string;
}

export interface IntegrationStatus {
  google_calendar_connected: boolean;
  google_calendar_email: string;
  google_calendar_error: string;
  vapi_assistant_configured: boolean;
  whatsapp_configured: boolean;
}

export interface BusinessLabels {
  customer_singular: string;
  customer_plural: string;
  staff_singular: string;
  staff_plural: string;
  booking_singular: string;
  booking_plural: string;
}

export interface IntakeField {
  key: string;
  label: string;
  required: boolean;
  guidance: string;
}

/** A starting point offered on the onboarding form. Every value stays editable. */
export interface BusinessTypePreset {
  slug: string;
  display_name: string;
  default_agent_name: string;
  business_descriptor: string;
  labels: BusinessLabels;
  intake_fields: IntakeField[];
  rules: string[];
  escalation: string;
  example_services: string[];
  default_slot_minutes: number;
}

export interface Business {
  id: string;
  name: string;
  slug: string;
  address: string;
  city: string;
  contact_phone: string;
  contact_email: string;
  timezone: string;

  /** Business type configuration. Seeded from a preset, then owned by the tenant. */
  business_type: string;
  business_descriptor: string;
  labels: BusinessLabels;
  intake_fields: IntakeField[];
  agent_rules: string[];
  escalation_instructions: string;

  /** 0 means keep indefinitely. */
  transcript_retention_days: number;
  recording_retention_days: number;

  agent_name: string;
  /** The number customers dial. Not editable from settings. */
  phone_number: string;
  primary_language: Language;
  greeting_en: string;
  greeting_hi: string;
  agent_notes: string;

  /** "HH:MM:SS" in the business's own timezone. */
  opens_at: string;
  closes_at: string;
  /** ISO weekday numbers: 1 = Monday ... 7 = Sunday. */
  working_days: number[];
  slot_duration_minutes: number;

  whatsapp_enabled: boolean;
  reminder_24h_enabled: boolean;
  reminder_2h_enabled: boolean;
  is_active: boolean;

  integrations: IntegrationStatus | null;
  staff_members: StaffMember[];
}

export interface BusinessUpdateRequest {
  name?: string;
  business_type?: string;
  business_descriptor?: string;
  labels?: BusinessLabels;
  intake_fields?: IntakeField[];
  agent_rules?: string[];
  escalation_instructions?: string;
  transcript_retention_days?: number;
  recording_retention_days?: number;
  address?: string;
  city?: string;
  contact_phone?: string;
  contact_email?: string;
  timezone?: string;
  agent_name?: string;
  primary_language?: Language;
  greeting_en?: string;
  greeting_hi?: string;
  agent_notes?: string;
  opens_at?: string;
  closes_at?: string;
  working_days?: number[];
  slot_duration_minutes?: number;
  whatsapp_enabled?: boolean;
  reminder_24h_enabled?: boolean;
  reminder_2h_enabled?: boolean;
}

export interface StaffMemberCreateRequest {
  name: string;
  specialization?: string;
  google_calendar_id?: string;
  consultation_duration_minutes?: number;
}

export type StaffMemberUpdateRequest = Partial<StaffMemberCreateRequest> & {
  is_active?: boolean;
};

// --------------------------------------------------------------------------- //
// Customers
// --------------------------------------------------------------------------- //
export interface CustomerSummary {
  id: string;
  name: string;
  phone: string;
}

export interface Customer extends CustomerSummary {
  email: string;
  preferred_language: Language;
  notes: string;
  created_at: string;
}

export interface CustomerDetail extends Customer {
  appointments: Array<{
    id: string;
    starts_at: string;
    status: AppointmentStatus;
    reason: string;
  }>;
  total_appointments: number;
  upcoming_appointments: number;
}

export interface CustomerUpdateRequest {
  name?: string;
  email?: string;
  preferred_language?: Language;
  notes?: string;
}

// --------------------------------------------------------------------------- //
// Appointments
// --------------------------------------------------------------------------- //
export interface Slot {
  /** UTC ISO-8601. Pass back verbatim when booking. */
  starts_at: string;
  ends_at: string;
  /** Pre-formatted in the business's timezone, safe to render directly. */
  label: string;
  staff_member_id: string | null;
  staff_member_name: string;
}

export interface Appointment {
  id: string;
  status: AppointmentStatus;
  /** UTC ISO-8601. */
  starts_at: string;
  ends_at: string;
  /** Same instant, pre-rendered in the business's timezone. Prefer this for display. */
  starts_at_local: string;
  reason: string;
  notes: string;
  cancellation_reason: string;
  customer: CustomerSummary;
  staff_member_id: string | null;
  staff_member_name: string;
  call_id: string | null;
  google_event_id: string;
  synced_to_calendar: boolean;
  rescheduled_from_id: string | null;
  created_at: string;
}

export interface AppointmentCreateRequest {
  customer_name: string;
  /** E.164, e.g. "+919876543210". */
  customer_phone: string;
  /** ISO-8601 including an offset. A naive value is rejected by the API. */
  starts_at: string;
  staff_member_id?: string | null;
  appointment_type_id?: string | null;
  duration_minutes?: number;
  reason?: string;
  notes?: string;
  preferred_language?: Language;
}

export interface AppointmentRescheduleRequest {
  starts_at: string;
  staff_member_id?: string | null;
  reason?: string;
}

export interface AppointmentCancelRequest {
  reason?: string;
  notify_customer?: boolean;
}

// --------------------------------------------------------------------------- //
// Calls
// --------------------------------------------------------------------------- //
export interface Call {
  id: string;
  vapi_call_id: string;
  caller_number: string;
  started_at: string | null;
  ended_at: string | null;
  duration_seconds: number;
  language: Language;
  outcome: CallOutcome;
  summary: string;
  recording_url: string;
  ended_reason: string;
  customer: CustomerSummary | null;
  appointment_id: string | null;
  created_at: string;
}

/** The list endpoint omits `transcript` for payload size; detail includes it. */
export interface CallDetail extends Call {
  transcript: string;
  cost_paise: number;
}

// --------------------------------------------------------------------------- //
// WhatsApp
// --------------------------------------------------------------------------- //
export interface WhatsAppMessage {
  id: string;
  appointment_id: string | null;
  to_phone: string;
  kind: MessageKind;
  status: MessageStatus;
  template_name: string;
  language_code: string;
  /** The message as the customer sees it, with variables filled in. */
  rendered_preview: string;
  scheduled_for: string | null;
  sent_at: string | null;
  delivered_at: string | null;
  attempt_count: number;
  last_error: string;
  created_at: string;
}

// --------------------------------------------------------------------------- //
// Dashboard
// --------------------------------------------------------------------------- //
export interface DashboardStats {
  calls_total: number;
  calls_today: number;
  appointments_upcoming: number;
  appointments_today: number;
  booked_by_agent: number;
  cancelled: number;
  no_details: number;
  /** Percentage, 0-100, one decimal place. */
  conversion_rate: number;
  whatsapp_sent: number;
  whatsapp_failed: number;
  avg_call_duration_seconds: number;
}

// --------------------------------------------------------------------------- //
// Integrations
// --------------------------------------------------------------------------- //
/** Browser test-call config. Only the PUBLIC VAPI key is ever sent here. */
export interface TestCallConfig {
  public_key: string;
  assistant_id: string;
  agent_name: string;
  business_name: string;
}

export interface OutboundTestCallResult {
  call_id: string;
  status: string;
}

export interface WhatsAppSenderStatus {
  using_own_number: boolean;
  phone_number_id: string;
  display_number: string;
  has_access_token: boolean;
}

export interface PlatformSettingStatus {
  set: boolean;
  source: "dashboard" | "environment" | "none";
  /** Last four characters for secrets; the full value for non-secret ids. */
  preview: string;
}

export interface WhatsAppConfigStatus {
  settings: Record<string, PlatformSettingStatus>;
}

export interface WhatsAppTestResult {
  ok: boolean;
  detail: string;
  phone_number?: string;
  verified_name?: string;
  quality_rating?: string;
  templates_approved?: number;
}

export interface AdminTenantUser {
  id: string;
  email: string;
  full_name: string;
  role: string;
  last_login_at: string | null;
  must_change_password: boolean;
  is_locked: boolean;
}

export interface AdminTenantUsers {
  business: { id: string; name: string; slug: string };
  users: AdminTenantUser[];
}

/** A one-time password. The server will never return it again. */
export interface IssuedPassword {
  id: string;
  email: string;
  temporary_password: string;
}

export interface GoogleCalendarStatus {
  connected: boolean;
  email: string;
  calendar_id: string;
  last_error: string;
}

export interface GoogleAuthorizeResponse {
  authorization_url: string;
}

// --------------------------------------------------------------------------- //
// Admin (superadmin only, crosses tenant boundaries)
// --------------------------------------------------------------------------- //
export interface AdminBusinessRow {
  id: string;
  name: string;
  slug: string;
  business_type: string;
  phone_number: string;
  city: string;
  owner_email: string;
  is_active: boolean;
  created_at: string;
  calls_total: number;
  calls_last_7d: number;
  appointments_upcoming: number;
  /** The three things that must all be true before a client is live. */
  setup: {
    voice_agent: boolean;
    google_calendar: boolean;
    whatsapp: boolean;
  };
}

export interface PlatformStats {
  businesses_total: number;
  businesses_active: number;
  businesses_live: number;
  calls_total: number;
  appointments_total: number;
  call_cost_paise: number;
}

// --------------------------------------------------------------------------- //
// Onboarding
// --------------------------------------------------------------------------- //
export interface OnboardBusinessRequest {
  name: string;
  slug: string;
  phone_number: string;

  /**
   * Preset slug from GET /onboarding/business-types. It seeds everything below;
   * anything supplied explicitly overrides the preset's value.
   */
  business_type?: string;
  business_descriptor?: string;
  labels?: BusinessLabels;
  intake_fields?: IntakeField[];
  agent_rules?: string[];
  escalation_instructions?: string;

  owner_email: string;
  owner_password: string;
  owner_name?: string;
  address?: string;
  city?: string;
  contact_phone?: string;
  timezone?: string;
  agent_name?: string;
  primary_language?: Language;
  greeting_en?: string;
  greeting_hi?: string;
  opens_at?: string;
  closes_at?: string;
  working_days?: number[];
  slot_duration_minutes?: number;
  staff_members?: StaffMemberCreateRequest[];
  appointment_types?: string[];
  create_vapi_assistant?: boolean;
}

export interface OnboardBusinessResponse {
  business: Business;
  owner_user_id: string;
  vapi_assistant_id: string;
  /** Steps onboarding could not complete automatically. Render as a checklist. */
  next_steps: string[];
}

// --------------------------------------------------------------------------- //
// Query parameter shapes
// --------------------------------------------------------------------------- //
export interface PageQuery {
  page?: number;
  page_size?: number;
}

export interface CallListQuery extends PageQuery {
  outcome?: CallOutcome;
  search?: string;
}

export interface AppointmentListQuery extends PageQuery {
  status?: AppointmentStatus;
  staff_member_id?: string;
  date_from?: string;
  date_to?: string;
  search?: string;
}

export interface CustomerListQuery extends PageQuery {
  search?: string;
}

export interface MessageListQuery extends PageQuery {
  status?: MessageStatus;
  kind?: MessageKind;
  appointment_id?: string;
}

export interface AvailabilityQuery {
  date_from?: string;
  date_to?: string;
  staff_member_id?: string;
  duration_minutes?: number;
  limit?: number;
}

/** A list result after the client has unwrapped the envelope. */
export interface Paginated<T> {
  items: T[];
  meta: PaginationMeta;
}
