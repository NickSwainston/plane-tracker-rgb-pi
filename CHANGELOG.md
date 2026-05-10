## Update

I've updated all the weather scenes so if there is an error it'll just display ERR instead of freezing the clock.

Now logs the closest flights to your location and farthest destinations!

1. **Top N closest flights** to your location (`MAX_CLOSEST`)
2. **Top N farthest flights** based on origin or destination (`MAX_FARTHEST`)

Each time a flight is detected:

- Calculates the **distance from home**
- Updates `close.txt` and `farthest.txt` if a **new closest flight** or a **new top-N farthest flight** is found
- Sends an **automatic email alert** when these changes occur with flight details and map

**Email notifications:**

- Sent from `flight.tracker.alerts2025@gmail.com`
- Includes a **link to an interactive map** showing flight positions (Link is good for 30 days. You can always view the maps on your local IP page)

**Key details:**

- Adjustable limits with `MAX_CLOSEST` and `MAX_FARTHEST`
- Closest flights to your house are always updated in `close.txt`
- Farthest destination/origin flights are maintained in `farthest.txt` independently
- Alerts taper off as flight positions stabilize
- Emails can be **turned off** while still keeping the log files and local wegpage.

**New features:**

- Generates **interactive maps** for showing closest and farthest flights with generated curved Earth paths; solid for flown, dashed for remaining.

- Maps and log files can be viewed via your Pi’s local IP at `http://<Pi_IP>:8080` (The local IP address of your flight tracker ie 192.168.x.x:8080 etc)

This setup lets you stay updated without watching the clock, in addition to receiving email summaries with distance and map information.

If you would like to manually view the log files they are located here

```
nano ~/its-a-plane-python/close.txt
```
```
nano ~/its-a-plane-python/farthest.txt
```