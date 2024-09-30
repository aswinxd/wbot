from telethon import TelegramClient, events
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument
from telethon.errors import FloodWaitError, TimeoutError
import asyncio
import motor.motor_asyncio
import os
import subprocess

# Setup API and clients
api_id = "22181658"
api_hash = '3138df6840cbdbc28c370fd29218139a'
bot_token = '7022599037:AAFLJR-NI5vWD_7roOyCdo4RFq9oP8wEKZ8'
client = TelegramClient('user_session', api_id, api_hash)
bot = TelegramClient('bot_session', api_id, api_hash)
mongo_client = motor.motor_asyncio.AsyncIOMotorClient('mongodb+srv://mdalizadeh16:lavos@cluster0.u21tcwa.mongodb.net/?retryWrites=true&w=majority')
db = mongo_client['telegram_bot']
collection = db['schedules']
tasks = {}

# Limit concurrent downloads with Semaphore
semaphore = asyncio.Semaphore(5)  # Limit to 5 concurrent tasks

async def add_text_watermark(input_file, output_file, watermark_text):
    if not os.path.exists(input_file):
        return  # Skip if file doesn't exist
    
    # Validate media file format
    if not input_file.lower().endswith(('.png', '.jpg', '.jpeg', '.mp4', '.mkv', '.avi')):
        return  # Skip unsupported formats

    # Safely handle watermark text
    safe_watermark_text = watermark_text.replace("'", "\\'").replace("{", "\\{").replace("}", "\\}")
    
    command = [
        'ffmpeg', '-i', input_file,
        '-vf', f"drawtext=text='{safe_watermark_text}':fontcolor=white:fontsize=24:borderw=2:bordercolor=black:x=10:y=(h-text_h)/2",
        '-codec:a', 'copy', '-preset', 'fast',  # Faster encoding
        output_file
    ]
    
    # Run FFmpeg
    try:
        subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    except subprocess.CalledProcessError as e:
        return  # Handle errors silently

# Retry logic for media download
async def download_media_with_retry(message, retries=3, delay=5):
    for attempt in range(retries):
        try:
            return await client.download_media(message)
        except TimeoutError:
            if attempt + 1 == retries:
                return None  # Skip if fails after retries
            await asyncio.sleep(delay)

async def forward_messages(user_id, schedule_name, source_channel_id, destination_channel_id, batch_size, delay, caption, watermark_text):
    post_counter = 0
    max_duration = 20 * 60  # 20 minutes in seconds
    max_file_size = 100 * 1024 * 1024  # 100 MB in bytes

    async with client:
        async for message in client.iter_messages(int(source_channel_id), reverse=True):
            
            if tasks[user_id][schedule_name]['paused']:
                await asyncio.sleep(1)
                continue

            if post_counter >= batch_size:
                await asyncio.sleep(delay)
                post_counter = 0

            if isinstance(message.media, MessageMediaDocument):
                if hasattr(message.media, 'document'):
                    video_attrs = next((attr for attr in message.media.document.attributes if hasattr(attr, 'duration')), None)
                    file_size = message.media.document.size
                    
                    # Skip long or large files
                    if video_attrs and video_attrs.duration > max_duration:
                        continue
                    if file_size > max_file_size:
                        continue
                
                # Limit concurrent media downloads with semaphore
                async with semaphore:
                    media_file = await download_media_with_retry(message)
                    if media_file is None:
                        continue
                    
                    watermarked_file = f"watermarked_{os.path.basename(media_file)}"
                    await add_text_watermark(media_file, watermarked_file, watermark_text)
                    await client.send_file(int(destination_channel_id), watermarked_file, caption=caption)
                    post_counter += 1
                    os.remove(media_file)
                    os.remove(watermarked_file)
            
            if schedule_name not in tasks[user_id] or tasks[user_id][schedule_name]['task'].cancelled():
                break

@bot.on(events.NewMessage(pattern='/start'))
async def start(event):
    instructions = (
        "Here's how you can use it:\n\n"
        "1. **/add** - Set up a new message forwarding schedule.\n"
        "2. **/pause <schedule_name>** - Pause a currently running schedule.\n"
        "3. **/resume <schedule_name>** - Resume a paused schedule.\n"
        "4. **/stop <schedule_name>** - Stop and remove a schedule.\n\n"
        "For detailed setup, use `/add` to create a new schedule."
    )
    await event.respond(instructions)

@bot.on(events.NewMessage(pattern='/add'))
async def add_schedule(event):
    user_id = event.sender_id

    async with bot.conversation(user_id) as conv:
        await conv.send_message('Please provide a name for the schedule:')
        schedule_name = await conv.get_response()

        await conv.send_message('Please provide the source channel ID:')
        source_channel_id = await conv.get_response()

        await conv.send_message('Please provide the destination channel ID:')
        destination_channel_id = await conv.get_response()

        await conv.send_message('How many posts do you want to forward in each batch?')
        post_limit = await conv.get_response()

        await conv.send_message('What is the time interval between batches in seconds?')
        delay = await conv.get_response()

        await conv.send_message('Please provide a custom watermark text (leave blank if no watermark is needed):')
        watermark_text = await conv.get_response()

        await conv.send_message('Please provide a caption for the forwarded messages (leave blank if no caption is needed):')
        caption = await conv.get_response()

        # Confirmation and adding schedule
        await conv.send_message(f'Schedule created:\nName: {schedule_name.text}\nConfirm start? (yes/no)')
        confirmation = await conv.get_response()

        if confirmation.text.lower() != 'yes':
            await conv.send_message('Schedule setup cancelled.')
            return

        # Save schedule in DB and start task
        await collection.update_one(
            {'user_id': user_id},
            {'$push': {
                'schedules': {
                    'name': schedule_name.text,
                    'source_channel_id': int(source_channel_id.text),
                    'destination_channel_id': int(destination_channel_id.text),
                    'post_limit': int(post_limit.text),
                    'delay': int(delay.text),
                    'watermark': watermark_text.text,
                    'caption': caption.text
                }
            }},
            upsert=True
        )

        if user_id not in tasks:
            tasks[user_id] = {}

        task = asyncio.create_task(forward_messages(user_id, schedule_name.text, int(source_channel_id.text), int(destination_channel_id.text), int(post_limit.text), int(delay.text), caption.text, watermark_text.text))
        tasks[user_id][schedule_name.text] = {'task': task, 'paused': False}

@bot.on(events.NewMessage(pattern='/pause'))
async def pause_schedule(event):
    try:
        schedule_name = event.message.text.split()[1]
    except IndexError:
        await event.respond("Please provide a schedule name. Usage: /pause <schedule_name>")
        return

    user_id = event.sender_id
    if user_id in tasks and schedule_name in tasks[user_id]:
        tasks[user_id][schedule_name]['paused'] = True
        await event.respond(f"Schedule '{schedule_name}' has been paused.")
    else:
        await event.respond("No such schedule is running.")

@bot.on(events.NewMessage(pattern='/resume'))
async def resume_schedule(event):
    try:
        schedule_name = event.message.text.split()[1]
    except IndexError:
        await event.respond("Please provide a schedule name. Usage: /resume <schedule_name>")
        return

    user_id = event.sender_id
    if user_id in tasks and schedule_name in tasks[user_id]:
        tasks[user_id][schedule_name]['paused'] = False
        await event.respond(f"Schedule '{schedule_name}' has been resumed.")
    else:
        await event.respond("No such schedule is running.")

async def main():
    await client.start()
    await bot.start(bot_token=bot_token)
    await client.run_until_disconnected()
    await bot.run_until_disconnected()

if __name__ == '__main__':
    asyncio.run(main())
