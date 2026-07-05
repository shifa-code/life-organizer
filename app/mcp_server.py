import os
import json
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Life Organizer Server")

DATA_FILE = os.path.join(os.path.dirname(__file__), "life_organizer_data.json")

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"groceries": {}, "chores": []}

def save_data(data):
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

@mcp.tool()
def add_grocery_item(item: str, quantity: int = 1) -> str:
    """Add an item to the grocery list with a specified quantity.

    Args:
        item: The name of the grocery item (e.g. 'apples', 'milk').
        quantity: The quantity of the item to add.
    """
    data = load_data()
    item_lower = item.strip().lower()
    if item_lower in data["groceries"]:
        data["groceries"][item_lower] += quantity
    else:
        data["groceries"][item_lower] = quantity
    save_data(data)
    return f"Successfully added {quantity} x '{item}' to the grocery list."

@mcp.tool()
def get_grocery_list() -> str:
    """Retrieve the current grocery list.

    Returns:
        A text representation of the current grocery list.
    """
    data = load_data()
    groceries = data["groceries"]
    if not groceries:
        return "Your grocery list is empty."
    
    lines = ["Current Grocery List:"]
    for item, qty in groceries.items():
        lines.append(f"- {item}: {qty}")
    return "\n".join(lines)

@mcp.tool()
def clear_grocery_list() -> str:
    """Clear all items from the grocery list.

    Returns:
        Confirmation message that the list has been cleared.
    """
    data = load_data()
    data["groceries"] = {}
    save_data(data)
    return "Successfully cleared the grocery list."

@mcp.tool()
def add_chore(name: str, due_date: str) -> str:
    """Add a chore or maintenance task with a due date.

    Args:
        name: Description of the chore or home maintenance task (e.g. 'Change HVAC filters').
        due_date: The date by which it should be completed (e.g. '2026-07-15' or 'Tomorrow').
    """
    data = load_data()
    chore = {"name": name.strip(), "due_date": due_date.strip()}
    data["chores"].append(chore)
    save_data(data)
    return f"Successfully scheduled chore '{name}' due by {due_date}."

@mcp.tool()
def get_chore_list() -> str:
    """Retrieve the list of chores and home maintenance tasks.

    Returns:
        A text representation of the scheduled chores.
    """
    data = load_data()
    chores = data["chores"]
    if not chores:
        return "No chores or home maintenance tasks scheduled."
    
    lines = ["Scheduled Chores & Maintenance:"]
    for idx, chore in enumerate(chores, 1):
        lines.append(f"{idx}. {chore['name']} (Due: {chore['due_date']})")
    return "\n".join(lines)

if __name__ == "__main__":
    mcp.run()
