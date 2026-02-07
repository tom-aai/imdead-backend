# Stripe Setup Guide

This guide covers the configuration needed to enable subscription management (downgrade/cancel) and account deletion features.

## Quick Reference

**Already completed local development setup?** Jump to [Development vs Production Setup](#development-vs-production-setup) to learn how to configure production on Render.

### Environment Summary

| Environment | Backend URL | Stripe Keys | Webhook Setup | Use Case |
|------------|-------------|-------------|---------------|----------|
| **Local Dev** | `localhost:5001` | Test keys (`sk_test_`) | `stripe listen` CLI | Development & testing |
| **Production** | Render URL | Live keys (`sk_live_`) | Stripe Dashboard webhook | Real customers & payments |

**Key Points:**
- ✅ Both environments run simultaneously without conflict
- ✅ Local uses test mode (fake payments), production uses live mode (real payments)
- ✅ Each has its own Stripe configuration and webhook secret
- ✅ You can develop locally while production serves real customers

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

Tom. you tested everything in the sandbox already. these are the 5 you need for live. use 'Your Account'.
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

## Development vs Production Setup

This section explains how to run Stripe in both local development (with test mode) and production (with live payments) simultaneously.

### Overview

You'll maintain **two completely separate Stripe configurations**:

1. **Development (Local):**
   - Uses Stripe **test mode** keys (`sk_test_xxx`)
   - Backend runs on `localhost:5001`
   - Webhooks forwarded via `stripe listen` CLI
   - Use test credit cards (no real money)

2. **Production (Render):**
   - Uses Stripe **live mode** keys (`sk_live_xxx`)
   - Backend runs on your Render URL
   - Webhooks configured in Stripe Dashboard
   - Real payments from real customers

These environments are **completely independent** - you can develop locally while production runs simultaneously.

### Step 1: Verify Local Development Setup

Your local setup should already be working. Verify you have:

**File: `.env.local`** (in `expert-avatar-backend/`)
```bash
# Stripe TEST keys (keep these for local development)
STRIPE_SECRET_KEY=sk_test_51xxxxx
STRIPE_WEBHOOK_SECRET=whsec_xxxxx  # From `stripe listen` output
STRIPE_PRICE_ID=price_xxxxx  # Your test mode price ID

# Local app URL
APP_URL=http://localhost:3000

# Other credentials...
SUPABASE_URL=...
SUPABASE_SERVICE_KEY=...
# etc
```

**Terminal 1:** Run backend
```bash
cd expert-avatar-backend
python app.py
# Runs on localhost:5001
```

**Terminal 2:** Forward webhooks
```bash
stripe listen --forward-to localhost:5001/api/billing/webhook
# This outputs your webhook secret - use it in .env.local
```

### Step 2: Set Up Production on Render

#### A. Get Your Render Backend URL

1. Go to [Render Dashboard](https://dashboard.render.com)
2. Find your `expertavatar-api` service
3. Copy the URL (looks like: `https://expertavatar-api-xxxxx.onrender.com`)

#### B. Create a Live Webhook Endpoint in Stripe

1. Go to [Stripe Dashboard > Developers > Webhooks](https://dashboard.stripe.com/webhooks)
2. **IMPORTANT:** Make sure you're in **live mode** (toggle in top right should say "Live")
3. Click "Add endpoint"
4. **Endpoint URL:** `https://your-render-url.onrender.com/api/billing/webhook`
5. **Listen to:** Select "Your Account"
6. **Events to send:**
   - `checkout.session.completed`
   - `customer.subscription.updated`
   - `customer.subscription.deleted`
   - `invoice.paid`
   - `invoice.payment_failed`
7. Click "Add endpoint"
8. **Copy the Signing Secret** (starts with `whsec_`) - you'll need this for Render

#### C. Get Your Live Stripe Keys

1. Go to [Stripe Dashboard > Developers > API Keys](https://dashboard.stripe.com/apikeys)
2. **IMPORTANT:** Make sure you're in **live mode** (toggle in top right)
3. Copy your **Secret key** (starts with `sk_live_`)
4. Go to [Products](https://dashboard.stripe.com/products) and find your subscription product
5. Copy the **Price ID** (starts with `price_`)

#### D. Configure Render Environment Variables

1. Go to Render Dashboard > `expertavatar-api` service > Environment
2. Add/update these variables:

```bash
# Stripe LIVE keys (for production)
STRIPE_SECRET_KEY=sk_live_51xxxxx  # From step C
STRIPE_WEBHOOK_SECRET=whsec_xxxxx   # From step B
STRIPE_PRICE_ID=price_xxxxx         # From step C (live mode price)

# Production app URL
APP_URL=https://expertavatar.com

# All other env vars should already be set...
```

3. Save changes - Render will automatically redeploy

### Step 3: Configure Stripe Customer Portal (Live Mode)

1. Go to [Stripe Dashboard > Settings > Billing > Customer Portal](https://dashboard.stripe.com/settings/billing/portal)
2. **IMPORTANT:** Make sure you're in **live mode**
3. Enable:
   - "Allow customers to view invoice history"
   - "Allow customers to update payment methods"
   - "Allow customers to cancel subscriptions"
4. Set **Return URL:** `https://expertavatar.com/settings`
5. Save changes

### Step 4: Testing Production

You have two options for testing production:

#### Option A: Test Mode in Production (Recommended First)

You can test the production deployment without real money by temporarily using test keys:

1. Temporarily set Render env vars to use `sk_test_` keys
2. Create a test webhook endpoint in Stripe (test mode) pointing to Render
3. Use [Stripe test cards](https://stripe.com/docs/testing)
4. Verify everything works
5. Switch to live keys when ready

#### Option B: Go Live with Real Payments

1. Use live keys (already configured in Step 2)
2. Test with a real card (charge yourself $1 to verify)
3. Cancel the subscription immediately if just testing
4. Monitor [Stripe Dashboard > Payments](https://dashboard.stripe.com/payments)

### Step 5: Verify Production Setup

Test your production endpoint:

```bash
# Check health endpoint
curl https://your-render-url.onrender.com/health

# Test that backend is using correct Stripe mode
# (Make a test API call from your frontend while watching Render logs)
```

**Check Render Logs:**
1. Go to Render Dashboard > `expertavatar-api` service > Logs
2. Look for any Stripe-related errors on startup
3. Trigger a webhook event in Stripe Dashboard and watch for webhook processing logs

### Production Setup Checklist

Use this checklist to ensure everything is configured correctly:

**Stripe Dashboard (Live Mode):**
- [ ] Created live webhook endpoint pointing to `https://your-render-url.onrender.com/api/billing/webhook`
- [ ] Added all 5 required webhook events (checkout.session.completed, customer.subscription.updated, etc.)
- [ ] Copied webhook signing secret (starts with `whsec_`)
- [ ] Copied live secret key (starts with `sk_live_`)
- [ ] Copied live price ID (starts with `price_`)
- [ ] Configured Customer Portal with return URL: `https://expertavatar.com/settings`
- [ ] Enabled customer portal features (view invoices, update payment, cancel subscription)

**Render Dashboard:**
- [ ] Set `STRIPE_SECRET_KEY` to live key (`sk_live_xxx`)
- [ ] Set `STRIPE_WEBHOOK_SECRET` to webhook signing secret from live endpoint
- [ ] Set `STRIPE_PRICE_ID` to live price ID
- [ ] Set `APP_URL` to `https://expertavatar.com`
- [ ] Service successfully deployed after adding env vars

**Testing:**
- [ ] Health endpoint responds: `curl https://your-render-url.onrender.com/health`
- [ ] Render logs show no Stripe configuration errors
- [ ] Test webhook in Stripe Dashboard shows successful delivery
- [ ] (Optional) Create test subscription and verify webhook events process correctly

### Understanding the Dual Environment

```
┌─────────────────────────────────────────────────────────────────────┐
│                         DEVELOPMENT (Local)                          │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  Your Computer                                                       │
│  ┌──────────────────┐         ┌─────────────────┐                  │
│  │  Frontend        │────────▶│  Backend        │                  │
│  │  localhost:3000  │         │  localhost:5001 │                  │
│  └──────────────────┘         └────────┬────────┘                  │
│                                         │                            │
│                                         │ Test Keys                  │
│                                         │ (sk_test_xxx)             │
│                                         ▼                            │
│                              ┌──────────────────┐                   │
│                              │  Stripe CLI      │                   │
│  ┌──────────────────────┐   │  stripe listen   │                   │
│  │  Stripe Dashboard    │◀──┤  (forwards       │                   │
│  │  (Test Mode)         │   │   webhooks)      │                   │
│  └──────────────────────┘   └──────────────────┘                   │
│                                                                      │
│  ✓ Fake credit cards   ✓ Test customers   ✓ No real money          │
└──────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                        PRODUCTION (Render)                           │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  Internet                                                            │
│  ┌──────────────────────┐         ┌─────────────────────┐          │
│  │  Frontend            │────────▶│  Backend (Render)   │          │
│  │  expertavatar.com    │         │  your-render-url    │          │
│  └──────────────────────┘         └──────────┬──────────┘          │
│                                               │                      │
│                                               │ Live Keys            │
│                                               │ (sk_live_xxx)       │
│                                               ▼                      │
│                                    ┌──────────────────┐             │
│  ┌──────────────────────┐         │  Webhook         │             │
│  │  Stripe Dashboard    │────────▶│  Endpoint        │             │
│  │  (Live Mode)         │         │  /api/billing/   │             │
│  └──────────────────────┘         │  webhook         │             │
│                                    └──────────────────┘             │
│                                                                      │
│  ✓ Real credit cards   ✓ Real customers   ✓ Real payments          │
└──────────────────────────────────────────────────────────────────────┘
```

Here's what happens when you work with both environments:

| Action | Development (Local) | Production (Render) |
|--------|-------------------|-------------------|
| Run backend | `python app.py` on port 5001 | Automatically runs on Render |
| Stripe keys | Test keys (`sk_test_`) | Live keys (`sk_live_`) |
| Webhooks | Forwarded via `stripe listen` | Sent directly from Stripe to Render URL |
| Customers | Test customers only | Real customers |
| Payments | Fake credit cards | Real credit cards |
| Data | Local Supabase or dev database | Production Supabase |

**They never conflict because:**
- Different API keys = different Stripe accounts (test vs live)
- Different URLs = different webhook endpoints
- Different databases = separate customer data

### Common Workflows

**Developing a new feature locally:**
```bash
# Terminal 1: Run backend
cd expert-avatar-backend
python app.py

# Terminal 2: Forward webhooks
stripe listen --forward-to localhost:5001/api/billing/webhook

# Terminal 3: Run tests
uv run pytest tests/unit/test_billing_service.py -v

# Make changes, test locally, commit when ready
```

**Deploying to production:**
```bash
git push origin main
# Render automatically deploys
# Production uses live keys from environment variables
# No webhook forwarding needed - webhooks go directly to Render
```

**Testing a webhook in production:**
1. Go to [Stripe Dashboard > Developers > Webhooks](https://dashboard.stripe.com/webhooks) (live mode)
2. Click on your production endpoint
3. Click "Send test webhook"
4. Watch Render logs to see it process

## Troubleshooting

### Portal session returns None

Possible causes:
- User doesn't have a `stripe_customer_id` (never upgraded)
- `STRIPE_SECRET_KEY` not configured
- Stripe API error (check logs)

### Webhook events not being received

**Local Development:**
1. Is `stripe listen` running?
2. Is the webhook secret in `.env.local` correct?
3. Check terminal output from `stripe listen` for errors

**Production:**
1. Verify webhook URL is correct in Stripe Dashboard (live mode)
2. Check that the webhook secret matches in Render env vars
3. Ensure the events are enabled
4. Check Render logs for webhook processing errors
5. Test using "Send test webhook" in Stripe Dashboard

### Account deletion fails

Check the backend logs for specific errors. Common issues:
- Database constraint violations (check FK relationships)
- Stripe API errors when cancelling subscription
- Supabase auth admin permissions

### Different behavior in production vs local

- **Check environment variables:** Make sure Render has all required env vars set
- **Check Stripe mode:** Confirm you're using live keys in production, test keys locally
- **Check logs:** Render logs vs local terminal output
- **Check webhook secrets:** Each environment has its own webhook secret
