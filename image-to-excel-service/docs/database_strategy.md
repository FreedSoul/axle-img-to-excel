# Database Strategy & Image Organization Plan

This document summarizes the strategy for extracting weigh ticket data into a database and organizing the source images for long-term retrieval and audit.

## 1. Database Schema (`WeighTickets` table)

The following structure is designed to facilitate reporting on billing, inventory, and logistics efficiency.

| Column Name | Data Type | Description |
| :--- | :--- | :--- |
| `ticket_number` | Integer (PK) | Unique ID for the transaction (Unique identifier). |
| `transaction_date` | Date | Date of the transaction (YYYY-MM-DD). |
| `transaction_time` | Time | Time of the transaction (usually the "Out" time). |
| `customer_name` | Varchar | Who the product is for (e.g., Maschmeyer Concrete). |
| `job_location` | Varchar | Destination of the material. |
| `truck_id` | Varchar | Vehicle ID (Physical tag or internal code). |
| `hauler_company` | Varchar | The transportation company. |
| `product_name` | Varchar | Material description (e.g., Concrete Screenings). |
| `net_weight_tons` | Decimal | The billed amount in Tons. |
| `gross_weight_lbs` | Integer | Total weight (Truck + Load). |
| `tare_weight_lbs` | Integer | Empty truck weight. |
| `receiver_signature`| Varchar | Name or "Signature on File" of the receiver. |
| `scan_path` | Varchar | **Relative path** to the stored image file. |

## 2. File Naming Convention

To ensure uniqueness and human-readability, all processed images should be renamed following this pattern:

**Pattern:** `YYYY-MM-DD_{Vendor}_{TicketNumber}.jpg`

*Example:* `2026-01-14_PalmBeachAggregates_5261472.jpg`

### Benefits:
*   **Sortable**: Files automatically line up chronologically.
*   **Searchable**: Ticket numbers are easily found via system search.
*   **Groupable**: Vendor-specific records stay together within a date.

## 3. Storage Hierarchy (Folder Structure)

Avoid bulk folders. Use a year/month hierarchy for better performance and manual browsing.

```text
/WeighTickets
    /2026
        /01
            2026-01-13_CEMEX_1036166633.jpg
            2026-01-14_PalmBeachAggregates_5261472.jpg
        /02
            ...
```

## 4. Implementation Logic Flow

1.  **OCR Processing**: Extract Date, Vendor, and Ticket Number from the raw image.
2.  **Generate Filename**: Create the specialized filename string.
3.  **Directory Management**: Check for existence of `/Year/Month/` folders; create them if missing.
4.  **Move & Rename**: Transfer the file from the intake area to the permanent hierarchy.
5.  **Database Injection**: Insert the extracted textual data along with the `scan_path` (e.g., `2026/01/...`) into the SQL database.
