# CLAUDE.md

# TallyFlow (Working Name)

> AI-powered Mobile Dashboard for TallyPrime

---

# Project Vision

Build a modern mobile-first dashboard for TallyPrime that allows business owners to securely monitor their accounting and inventory data from anywhere.

The initial version (MVP) is **READ ONLY**.

Users can:

- View reports
- View dashboards
- View stock
- View receivables/payables
- View analytics
- View charts

Users **cannot modify any data** inside Tally.

The architecture, however, must be designed from day one so that future versions can support:

- Voucher Creation
- Ledger Creation
- Stock Management
- AI Assistant
- Voice Commands
- Notifications
- Workflow Automation

without requiring major architectural changes.

---

# Primary Goal

Create the best mobile companion for TallyPrime.

Think of it as:

> "Google Analytics for Tally"

instead of

> "Remote Desktop for Tally"

The user should never feel like they are using Tally.

Instead, they should experience a modern SaaS dashboard.

---

# Product Principles

## 1. Read First

Everything is read-only.

No mutations.

No accidental accounting changes.

---

## 2. Mobile First

The application is designed for mobile devices.

Desktop dashboard comes later.

---

## 3. Near Real-Time

Whenever possible, display fresh data from Tally.

Avoid unnecessary database duplication.

---

## 4. Security First

Accounting data is sensitive.

Every architectural decision should prioritize:

- encryption
- authentication
- authorization
- audit logs
- secure transport

---

## 5. Offline Friendly

If Tally is offline:

- show last synced data
- notify user
- automatically reconnect

Never crash.

---

## 6. Future Proof

Every module should later support:

READ

↓

WRITE

without redesign.

Example:

Current

DashboardService

↓

Future

DashboardService
VoucherService
LedgerService
StockService

---

# MVP Scope

Only implement:

✅ Authentication

✅ Company Connection

✅ Dashboard

✅ Reports

✅ Analytics

✅ Charts

✅ Inventory

✅ Outstanding

✅ Ledger Summary

✅ Sales Summary

✅ Purchase Summary

❌ No Create APIs

❌ No Update APIs

❌ No Delete APIs

---

# Inspiration

## SetuBi

Website

https://www.setubi.in/

Study carefully:

- UX
- Dashboard
- Reports
- Mobile Layout
- Navigation
- Business Flow

Do NOT clone.

Instead:

Understand how accountants consume information.

Build a significantly better experience.

---

# Official References

## TallyPrime API Explorer

https://tallysolutions.com/tallyprime-api-explorer/

This is the primary reference.

Always prefer official examples.

---

## Tally JSON Integration

https://help.tallysolutions.com/tally-prime-integration-using-json-1/

Must understand:

- Export
- Import
- Headers
- Request Types
- Collections
- Objects
- Static Variables

---

## Tally Developer Documentation

https://help.tallysolutions.com/

---

# Architecture

                    Flutter App
                          │
                          │ HTTPS
                          ▼
                  Backend API Server
                          │
                    Secure WebSocket
                          │
                          ▼
                Windows Connector
                          │
                    localhost:9000
                          │
                          ▼
                     TallyPrime

---

# Why this architecture?

Never expose Tally directly to the internet.

The connector should be the only component communicating with Tally.

Benefits:

- secure
- scalable
- cloud independent
- supports multiple users
- future AI support

---

# System Components

## Flutter Application

Responsibilities

- Authentication
- Dashboard
- Charts
- Reports
- Notifications
- User Preferences

Never communicate directly with Tally.

---

## Backend API

Responsibilities

Authentication

Authorization

Companies

Subscriptions

Users

Analytics

Caching

Push Notifications

Audit Logs

Future AI APIs

Future Web Dashboard

Future Public APIs

---

## Windows Connector

This is the heart of the system.

Responsibilities

- Connect to Tally
- Read data
- Convert responses
- Authenticate with backend
- Maintain secure connection
- Cache responses
- Retry failures

The connector should NOT contain business logic.

It should simply translate requests.

Think of it as:

Backend

↓

Connector

↓

Tally

---

# Connector Responsibilities

Should support

Health Check

Ping

Authentication

Heartbeat

Reconnect

Queue

Compression

Encryption

Version Updates

Logging

Diagnostics

Future Plugin Updates

---

# Communication Flow

Flutter

↓

Backend

↓

Connector

↓

Tally

↓

Connector

↓

Backend

↓

Flutter

---

# Future WebSocket Flow

Instead of polling

Flutter

↓

Backend

↓

WebSocket

↓

Connector

↓

Tally

Realtime Updates

---

# Authentication

Future support:

JWT

Refresh Tokens

Multiple Companies

Multiple Users

Role Permissions

Device Management

---

# Data Strategy

Current

Read directly from Tally.

Future

Read

↓

Cache

↓

Analytics Database

↓

AI

↓

Predictions

Never tightly couple UI to Tally responses.

Always convert responses into internal DTOs.

Example

Tally Response

↓

Mapper

↓

Domain Model

↓

API Response

↓

Flutter

---

# Domain Modules

Dashboard

Reports

Ledger

Inventory

Sales

Purchase

Payments

Receipts

Bank

Cash

Analytics

Notifications

Settings

Users

Companies

Connector

Future AI

Future Automation

---

# Dashboard Widgets

Today's Sales

Today's Purchase

Monthly Sales

Monthly Purchase

Cash Balance

Bank Balance

Outstanding Receivables

Outstanding Payables

Inventory Value

Top Customers

Top Products

Sales Trend

Purchase Trend

Expense Breakdown

Profit Overview

Recent Transactions

GST Summary

Company Health

---

# Reports

Day Book

Cash Book

Bank Book

Ledger

Outstanding

Stock Summary

Sales Register

Purchase Register

Profit & Loss

Balance Sheet

Trial Balance

Top Customers

Top Suppliers

Top Products

Inactive Products

Negative Stock

Negative Ledgers

---

# Future AI

Architecture should support

User

↓

LLM

↓

Tool Selection

↓

Backend

↓

Connector

↓

Tally

Possible prompts

Show today's sales.

Who owes me money?

Top customer this month.

Show low stock items.

Which products aren't selling?

Summarize this month's business.

---

# Future Write APIs

Not part of MVP.

Architecture must support:

Create Ledger

Create Voucher

Create Sales

Create Purchase

Create Receipt

Create Payment

Create Journal

Create Stock Item

Update Masters

Delete Masters

without redesign.

---

# Code Standards

Use Clean Architecture.

Feature First.

Avoid God Classes.

Repository Pattern.

Dependency Injection.

Domain Driven Naming.

Never expose Tally models to UI.

Always use DTOs.

---

# Performance

Never request unnecessary data.

Support pagination.

Cache frequently requested reports.

Lazy loading.

Background refresh.

Compression.

---

# Security

HTTPS everywhere.

Encrypted Connector Communication.

JWT Authentication.

Signed Requests.

No Tally exposed publicly.

No credentials stored in plaintext.

Audit every request.

---

# Logging

Connector Logs

API Logs

Authentication Logs

Sync Logs

Crash Logs

Performance Logs

Future Telemetry

---

# Error Handling

User-friendly errors.

Automatic retry.

Connector offline detection.

Graceful degradation.

No application crashes.

---

# Future Roadmap

Phase 1

Read-only dashboards

Phase 2

Advanced Analytics

Phase 3

Notifications

Phase 4

AI Chat

Phase 5

Write APIs

Phase 6

Automation

Phase 7

Voice Assistant

Phase 8

Marketplace

---

# Development Philosophy

Every decision should answer:

Can this scale to 100,000 companies?

Can this support AI later?

Can this support write APIs later?

Can this support plugins later?

Can this support automation later?

If the answer is "no", redesign before implementing.

---

# Non-Goals (MVP)

❌ Accounting Software

❌ ERP Replacement

❌ Remote Desktop

❌ Tally Clone

This product is a **business intelligence and mobile companion** for TallyPrime.

---

# Success Criteria

The application should allow a business owner to open their phone and, within 10 seconds, know:

- How much did I sell today?
- Who owes me money?
- What is my cash position?
- Which products are running out?
- What is today's profit?
- What happened since yesterday?

without opening TallyPrime.