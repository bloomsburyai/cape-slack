# Copyright (c) 2017 Blemundsbury AI Limited
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
# LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
# WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

import os
import time
from bots import bots
from slackclient import SlackClient
from cape.client import CapeClient, CapeException


READ_WEBSOCKET_DELAY = 1 # Delay in seconds between reading from firehose
API_BASE='https://responder.thecape.ai/api'

cc = CapeClient(API_BASE)
previous_answers = {}
last_answer = {}
previous_replies = {}


def handle_question(question, channel, bot, slack_client):
    cape_token = bot['cape_token']
    answers = cc.answer(question, cape_token, number_of_items=5)
    if len(answers) > 0:
        print(question, answers[0]['answerText'])
        previous_answers[bot['slack_key']] = answers
        last_answer[bot['slack_key']] = 0
        if answers[0]['sourceType'] == 'saved_reply':
            previous_replies[bot['slack_key']] = answers[0]['sourceId']
        else:
            previous_replies[bot['slack_key']] = None
        slack_client.api_call("chat.postMessage", channel=channel,
                              text="%s (confidence: %0.2f)" % (answers[0]['answerText'], answers[0]['confidence']), as_user=True)
    else:
        slack_client.api_call("chat.postMessage", channel=channel,
                              text="Sorry! I don't know the answer to that.", as_user=True)


def add_saved_reply(message, channel, bot, slack_client):
    try:
        message = message.split(".add-saved-reply")[1]
        question, answer = message.split('|', 1)
    except Exception as e:
        slack_client.api_call("chat.postMessage", channel=channel,
                              text="Sorry, I didn't understand that. The usage for .add-saved-reply is: .add-saved-reply question | answer", as_user=True)
        print("Add saved reply failed: ", e, "Message: ", message)
        return
    
    try:
        cape_admin_token = bot['cape_admin_token']
        admin_client = CapeClient(API_BASE, admin_token=cape_admin_token)
        reply_id = admin_client.add_saved_reply(question, answer)['replyId']
        previous_replies[bot['slack_key']] = reply_id
        slack_client.api_call("chat.postMessage", channel=channel,
                              text="Thanks, I'll remember that!", as_user=True)
    except CapeException as e:
        slack_client.api_call("chat.postMessage", channel=channel,
                              text=e.message, as_user=True)


def explain(channel, bot, slack_client):
    if bot['slack_key'] not in previous_answers:
        return
    previous = previous_answers[bot['slack_key']][last_answer[bot['slack_key']]]
    if previous['sourceType'] == 'document':
        slack_client.api_call("chat.postMessage", channel=channel,
                              text="I found that in the document '%s'" % previous['sourceId'], as_user=True)
    else:
        try:
            cape_admin_token = bot['cape_admin_token']
            admin_client = CapeClient(API_BASE, admin_token=cape_admin_token)
            saved_reply = admin_client.get_saved_replies(saved_reply_ids=[previous['sourceId']])['items'][0]
            slack_client.api_call("chat.postMessage", channel=channel,
                                  text="I thought you asked: %s" % saved_reply['canonicalQuestion'], as_user=True)
        except CapeException as e:
            slack_client.api_call("chat.postMessage", channel=channel,
                                  text=e.message, as_user=True)


def add_paraphrase(message, channel, bot, slack_client):
    if bot['slack_key'] not in previous_replies or previous_replies[bot['slack_key']] is None:
        slack_client.api_call("chat.postMessage", channel=channel,
                              text="Please ask a question or create a new saved reply so that I know what to add a paraphrase to.", as_user=True)
        return
    try:
        question = message.split(".add-paraphrase")[1]
        reply_id = previous_replies[bot['slack_key']]
        cape_admin_token = bot['cape_admin_token']
        admin_client = CapeClient(API_BASE, admin_token=cape_admin_token)
        admin_client.add_paraphrase_question(reply_id, question)
        slack_client.api_call("chat.postMessage", channel=channel,
                              text="Thanks, I'll remember that!", as_user=True)
    except CapeException as e:
        slack_client.api_call("chat.postMessage", channel=channel,
                              text=e.message, as_user=True)


def handle_next(channel, bot, slack_client):
    if bot['slack_key'] not in previous_answers:
        return
    last = last_answer[bot['slack_key']]
    answers = previous_answers[bot['slack_key']]
    last += 1
    if last < len(answers):
        slack_client.api_call("chat.postMessage", channel=channel,
                              text="%s (confidence: %0.2f)" % (answers[last]['answerText'], answers[last]['confidence']), as_user=True)
        last_answer[bot['slack_key']] = last
        if answers[0]['sourceType'] == 'saved_reply':
            previous_replies[bot['slack_key']] = answers[last]['sourceId']
        else:
            previous_replies[bot['slack_key']] = None
    else:
        slack_client.api_call("chat.postMessage", channel=channel,
                              text="I'm afraid I've run out of answers to that question.", as_user=True)


def context(channel, bot, slack_client):
    if bot['slack_key'] not in previous_answers:
        return
    last = last_answer[bot['slack_key']]
    answers = previous_answers[bot['slack_key']]
    if last < len(answers) and 'answerContext' in answers[last]:
        slack_client.api_call("chat.postMessage", channel=channel,
                              text="%s" % answers[last]['answerContext'], as_user=True)
    else:
        slack_client.api_call("chat.postMessage", channel=channel,
                              text="Sorry, I don't have any more context for that.", as_user=True)


def parse_slack_output(slack_rtm_output, bot):
    """
        The Slack Real Time Messaging API is an events firehose.
        this parsing function returns None unless a message is
        directed at the Bot, based on its ID.
    """
    output_list = slack_rtm_output
    if output_list and len(output_list) > 0:
        for output in output_list:
            at_bot = "<@%s>" % bot['bot_id']
            if output and 'text' in output and at_bot in output['text'] and 'channel' in output:
                # return text after the @ mention, whitespace removed
                return output['text'].split(at_bot)[1].strip(), \
                       output['channel']
    return None, None


if __name__ == "__main__":
    clients = {}

    while True:
        try:
            for bot in bots:
                clients[bot['slack_key']] = SlackClient(bot['slack_key'])
                if clients[bot['slack_key']].rtm_connect():
                    print("Connected bot: %s" % bot['name'])
                else:
                    print("Failed to connect bot: %s" % bot['name'])

            while True:
                for bot in bots:
                    message, channel = parse_slack_output(clients[bot['slack_key']].rtm_read(), bot)
                    if message and channel:
                        if message.lower().startswith(".add-saved-reply"):
                            add_saved_reply(message, channel, bot, clients[bot['slack_key']])
                        elif message.lower().startswith(".add-paraphrase"):
                            add_paraphrase(message, channel, bot, clients[bot['slack_key']])
                        elif message.lower().startswith(".explain"):
                            explain(channel, bot, clients[bot['slack_key']])
                        elif message.lower().startswith(".next"):
                            handle_next(channel, bot, clients[bot['slack_key']])
                        elif message.lower().startswith(".context"):
                            context(channel, bot, clients[bot['slack_key']])
                        else:
                            handle_question(message, channel, bot, clients[bot['slack_key']])
                time.sleep(READ_WEBSOCKET_DELAY)
        except Exception as e:
            print("Exception:", e)
            time.sleep(30)
            print("Attempting reconnection...")
