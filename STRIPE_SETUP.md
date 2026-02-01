# Stripe Setup Guide

This guide covers the configuration needed to enable subscription management (downgrade/cancel) and account deletion features.

## Stripe Dashboard Configuration

### 1. Enable Customer Portal

Navigate to [Stripe Dashboard > Settings > Billing > Customer Portal](https://dashboard.stripe.com/settings/billing/portal):

1. **Enable the following features:**
   - "Allow customers to view invoice history"
   - "Allow customers to update payment methods"
   - "Allow customers to cancel subscriptions"

2. **Configure cancellation options:**
   - Choose cancellation mode: "Cancel immediately" or "Cancel at end of billing period"
   - Optionally add a cancellation reason collection

3. **Set the return URL:**
   - Production: `https://expertavatar.com/settings`
   - Development: `http://localhost:3000/settings`

4. **Save changes**

### 2. Webhook Configuration

Your existing webhook at `/api/billing/webhook` already handles subscription lifecycle events. Ensure these events are enabled in [Stripe Dashboard > Developers > Webhooks](https://dashboard.stripe.com/webhooks):

Tom. these are the 5 you need. use 'Your Account'.
For the endpoint url, Go to your Render Dashboard
Find your expertavatar-api service
Copy the URL (looks like: https://expertavatar-api-XXXXX.onrender.com)
Add /api/billing/webhook to the end, https://your-production-backend.onrender.com/api/billing/webhook

**Required events:**
- `checkout.session.completed` - New subscription activated
- `customer.subscription.updated` - Subscription status changes
- `customer.subscription.deleted` - Subscription cancelled
- `invoice.paid` - Payment successful
- `invoice.payment_failed` - Payment failed

### 3. Required Environment Variables

Add these to your `.env` or environment configuration:

```bash
# Stripe API Keys
STRIPE_SECRET_KEY=sk_live_xxx  # or sk_test_xxx for testing
STRIPE_WEBHOOK_SECRET=whsec_xxx
STRIPE_PRICE_ID=price_xxx

# App URL for return redirects (used by checkout and portal sessions)
APP_URL=https://expertavatar.com
```

## Local Development Testing

### 1. Install Stripe CLI

```bash
# macOS
brew install stripe/stripe-cli/stripe

# Other platforms: https://stripe.com/docs/stripe-cli
```

### 2. Login and Forward Webhooks

```bash
# Login to your Stripe account
stripe login

# Forward webhooks to local backend
stripe listen --forward-to localhost:5001/api/billing/webhook
```

The CLI will output a webhook signing secret - use this as `STRIPE_WEBHOOK_SECRET` for local testing.

### 3. Test Portal Session Creation

```bash
# Create a test customer with subscription first (via the app or Stripe Dashboard)

# Test portal session creation
curl -X POST http://localhost:5001/api/billing/create-portal-session \
  -H "X-API-Key: your-backend-api-key" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "your-test-user-id"}'
```

### 4. Test Subscription Cancellation

```bash
# Trigger cancellation webhook manually
stripe trigger customer.subscription.deleted
```

### 5. Test Account Deletion

```bash
# Test account deletion endpoint
curl -X DELETE http://localhost:5001/api/user/account \
  -H "X-API-Key: your-backend-api-key" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "your-test-user-id"}'
```

### 6. Run Unit Tests

```bash
cd expert-avatar-backend
uv run pytest tests/unit/test_billing_service.py -v
```

## API Endpoints

### Subscription Management

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/billing/create-portal-session` | POST | Creates a Stripe Customer Portal session URL |
| `/api/billing/create-checkout-session` | POST | Creates a Stripe Checkout session for upgrades |
| `/api/billing/usage/<user_id>` | GET | Get user's current usage and billing status |
| `/api/billing/webhook` | POST | Handles Stripe webhook events |

### Account Deletion

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/user/account` | DELETE | Deletes user account and all associated data |

## Billing Status Values

The `billing_status` field in `user_profiles` can have these values:

| Status | Description |
|--------|-------------|
| `free` | Free tier user (default) |
| `active` | Active paid subscription |
| `past_due` | Payment failed, subscription at risk |
| `cancelled` | Subscription cancelled |

## Production Checklist

Before going live, verify:

- [ ] Customer Portal is enabled in Stripe Dashboard
- [ ] Cancellation policy is configured (immediate vs end of period)
- [ ] Return URL is set to production domain
- [ ] Webhook endpoint is verified in Stripe Dashboard
- [ ] All required subscription lifecycle events are enabled
- [ ] `APP_URL` environment variable is set correctly
- [ ] `STRIPE_SECRET_KEY` is the live key (not test)
- [ ] `STRIPE_WEBHOOK_SECRET` matches the production webhook
- [ ] Test mode verified before switching to live mode

## Account Deletion Flow

When a user deletes their account, the following happens in order:

1. **Cancel Stripe subscription** (if active)
2. **Delete chat_sessions** where `expert_user_id = user_id`
3. **Soft-delete avatar_share_tokens** (set `is_active = false`)
4. **Delete interview_call_analysis** records
5. **Delete interview_calls** records
6. **Delete user_profiles** record
7. **Delete Supabase auth user**

This ensures all user data is properly cleaned up while preserving the ability to show "avatar unavailable" messages for any previously shared links.

## Troubleshooting

### Portal session returns None

Possible causes:
- User doesn't have a `stripe_customer_id` (never upgraded)
- `STRIPE_SECRET_KEY` not configured
- Stripe API error (check logs)

### Webhook events not being received

1. Verify webhook URL is correct in Stripe Dashboard
2. Check that the webhook secret matches
3. Ensure the events are enabled
4. Use `stripe listen` locally to debug

### Account deletion fails

Check the backend logs for specific errors. Common issues:
- Database constraint violations (check FK relationships)
- Stripe API errors when cancelling subscription
- Supabase auth admin permissions
