from telethon import TelegramClient, events
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument
from telethon.errors import FloodWaitError
import asyncio
import motor.motor_asyncio
import os
import subprocess

api_id = "22181658"
api_hash = '3138df6840cbdbc28c370fd29218139a'
bot_token = '7022599037:AAFLJR-NI5vWD_7roOyCdo4RFq9oP8wEKZ8'
client = TelegramClient('user_session', api_id, api_hash)
bot = TelegramClient('bot_session', api_id, api_hash)
mongo_client = motor.motor_asyncio.AsyncIOMotorClient('mongodb+srv://mdalizadeh16:lavos@cluster0.u21tcwa.mongodb.net/?retryWrites=true&w=majority')
db = mongo_client['telegram_bot']
collection = db['schedules']
tasks = {} 


async def add_text_watermark(input_file, output_file, watermark_text):
    command = [
        'ffmpeg', '-i', input_file,
        '-vf', f"drawtext=text='{watermark_text}':fontcolor=white:fontsize=24:x=(w-text_w)/2:y=h-(text_h+10)",
        '-codec:a', 'copy', output_file
    ]
    try:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode != 0:
            print(f"FFmpeg error: {result.stderr.decode()}")
        else:
      #      print(f"Watermark added successfully to {output_file}")
    except Exception as e:
        print(f"Error applying watermark: {e}")


async def start_user_session():
    print("Starting user session...")
    await client.start()


async def forward_messages(user_id, schedule_name, source_channel_id, destination_channel_id, batch_size, delay, caption, watermark_text):
    post_counter = 0

    async with client:
        async for message in client.iter_messages(int(source_channel_id), reverse=True):
     
            if tasks[user_id][schedule_name]['paused']:
              #  print(f"Schedule {schedule_name} is paused. Waiting to resume...")
                await asyncio.sleep(1) 
                continue

            if post_counter >= batch_size:
                await asyncio.sleep(delay)
                post_counter = 0

            if isinstance(message.media, (MessageMediaPhoto, MessageMediaDocument)):
                try:
                    media_file = await client.download_media(message)
                    watermarked_file = f"watermarked_{os.path.basename(media_file)}"
                    await add_text_watermark(media_file, watermarked_file, watermark_text)
                    await client.send_file(int(destination_channel_id), watermarked_file, caption=caption)
                    post_counter += 1
                    os.remove(media_file)
                    os.remove(watermarked_file)
                except FloodWaitError as e:
                    print(f"FloodWaitError: Sleeping for {e.seconds} seconds")
                    await asyncio.sleep(e.seconds)
                except Exception as e:
                    print(f"An error occurred: {e}")
            if schedule_name not in tasks[user_id] or tasks[user_id][schedule_name]['task'].cancelled():
                break


@bot.on(events.NewMessage(pattern='/start'))
async def start(event):
    user_id = event.sender_id
    instructions = (
        "Here's how you can use it:\n\n"
        "1. **/add** - Set up a new message forwarding schedule.\n"
        "2. **/pause <schedule_name>** - Pause a currently running schedule.\n"
        "3. **/resume <schedule_name>** - Resume a paused schedule.\n"
        "4. **/stop <schedule_name>** - Stop and remove a schedule.\n\n"
        "For detailed setup, when you use `/add_schedule`, the bot will ask you for the following information:\n"
        "- **Schedule Name**: A name for the schedule.\n"
        "- **Source Channel ID**: The channel from where messages will be forwarded.\n"
        "- **Destination Channel ID**: The channel to where messages will be forwarded.\n"
        "- **Batch Size**: Number of messages to forward in each batch.\n"
        "- **Delay**: Time (in seconds) between each batch.\n"
        "- **Watermark Text**: Optional text to add to forwarded images and videos.\n"
        "- **Caption**: Optional caption to add to the forwarded messages.\n\n"
        "Use `/add` to get started with setting up a new schedule!"
    )
    await event.respond(instructions)
@bot.on(events.NewMessage(pattern='/add'))
async def start(event):
    user_id = event.sender_id

    async with bot.conversation(user_id) as conv:
        await conv.send_message('Please provide a name for the schedule:')
        schedule_name = await conv.get_response()

        await conv.send_message('Please provide the source channel ID:')
        source_channel_id = await conv.get_response()
        if not source_channel_id.text.lstrip('-').isdigit():
            await conv.send_message('Invalid channel ID. Please restart the process with /start.')
            return

        await conv.send_message('Please provide the destination channel ID:')
        destination_channel_id = await conv.get_response()
        if not destination_channel_id.text.lstrip('-').isdigit():
            await conv.send_message('Invalid channel ID. Please restart the process with /start.')
            return

        await conv.send_message('How many posts do you want to forward in each batch?')
        post_limit = await conv.get_response()
        if not post_limit.text.isdigit():
            await conv.send_message('Invalid number of posts. Please restart the process with /start.')
            return

        await conv.send_message('What is the time interval between batches in seconds?')
        delay = await conv.get_response()
        if not delay.text.isdigit():
            await conv.send_message('Invalid delay. Please restart the process with /start.')
            return

        await conv.send_message('Please provide a custom watermark text (leave blank if no watermark is needed):')
        watermark_text = await conv.get_response()

        await conv.send_message('Please provide a caption for the forwarded messages (leave blank if no caption is needed):')
        caption = await conv.get_response()

        await conv.send_message(f'You have set up the following schedule:\nSchedule Name: {schedule_name.text}\nSource Channel ID: {source_channel_id.text}\nDestination Channel ID: {destination_channel_id.text}\nPost Limit: {post_limit.text}\nDelay: {delay.text} seconds\nWatermark: "{watermark_text.text if watermark_text.text else "None"}"\nCaption: "{caption.text if caption.text else "None"}"\n\nDo you want to start forwarding? (yes/no)')
        confirmation = await conv.get_response()
        if confirmation.text.lower() != 'yes':
            await conv.send_message('Schedule setup cancelled.')
            return

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

        await conv.send_message(f'Forwarding messages from {source_channel_id.text} to {destination_channel_id.text} every {delay.text} seconds with watermark: "{watermark_text.text}" and caption: "{caption.text}"...')

        if user_id not in tasks:
            tasks[user_id] = {}

        task = asyncio.create_task(forward_messages(user_id, schedule_name.text, int(source_channel_id.text), int(destination_channel_id.text), int(post_limit.text), int(delay.text), caption.text, watermark_text.text))
        tasks[user_id][schedule_name.text] = {'task': task, 'paused': False}


@bot.on(events.NewMessage(pattern='/pause'))
async def pause_schedule(event):
    user_id = event.sender_id
    schedule_name = event.message.text.split()[1]

    if user_id in tasks and schedule_name in tasks[user_id]:
        tasks[user_id][schedule_name]['paused'] = True
        await event.respond(f"Schedule '{schedule_name}' has been paused.")
    else:
        await event.respond("No such schedule is running.")


@bot.on(events.NewMessage(pattern='/resume'))
async def resume_schedule(event):
    user_id = event.sender_id
    schedule_name = event.message.text.split()[1]

    if user_id in tasks and schedule_name in tasks[user_id]:
        tasks[user_id][schedule_name]['paused'] = False
        await event.respond(f"Schedule '{schedule_name}' has been resumed.")
    else:
        await event.respond("No such schedule is running.")


async def main():
    await start_user_session()
    print("User session started")
    await bot.start(bot_token=bot_token)
    print("Bot started")
    await client.run_until_disconnected()
    await bot.run_until_disconnected()


if __name__ == '__main__':
    asyncio.run(main())
